import logging
from core.schemas import ElementLevel
from core.ingestion.base import ArtifactProvider
from core.preprocessing.base import Preprocessor
from core.embedding.base import EmbeddingCreator
from core.storage.chroma_store import ChromaStore, SOURCE_COLLECTION, TARGET_COLLECTION
from core.classification.base import Classifier, ClassificationResult
from core.dependency import CodeDependencyAnalyzer, DependencyLinkExpander
from core.summarization import ElementSummarizer
from core.output.result_aggregator import ResultAggregator
from core.output.formatter import TraceMatrix

logger = logging.getLogger(__name__)


class TracePipeline:

    def __init__(
        self,
        source_provider: ArtifactProvider,
        target_provider: ArtifactProvider,
        source_preprocessor: Preprocessor,
        target_preprocessor: Preprocessor,
        embedder: EmbeddingCreator,
        classifier: Classifier,
        chroma_path: str = "./chroma_data",
        n_results: int = 10,
        source_output_level: ElementLevel | None = None,
        target_output_level: ElementLevel | None = None,
        dependency_analyzer: CodeDependencyAnalyzer | None = None,
        dependency_expansion_depth: int = 0,
        reset_vector_stores: bool = True,
        # Given only for sides whose artifacts are not prose. A requirement
        # already reads as a sentence, so summarising it costs tokens and adds
        # nothing the embedding did not already have.
        source_summarizer: ElementSummarizer | None = None,
        target_summarizer: ElementSummarizer | None = None,
    ):
        self.source_provider = source_provider
        self.target_provider = target_provider
        self.source_preprocessor = source_preprocessor
        self.target_preprocessor = target_preprocessor
        self.source_summarizer = source_summarizer
        self.target_summarizer = target_summarizer
        self.embedder = embedder
        self.classifier = classifier
        self.n_results = n_results
        self.dependency_analyzer = dependency_analyzer
        self.dependency_expansion_depth = dependency_expansion_depth
        self.reset_vector_stores = reset_vector_stores
        self.aggregator = ResultAggregator(source_output_level, target_output_level)
        self.source_store = ChromaStore(SOURCE_COLLECTION, chroma_path)
        self.target_store = ChromaStore(TARGET_COLLECTION, chroma_path)

    def run(self) -> TraceMatrix:

        logger.info("Loading artifacts")
        source_artifacts = self.source_provider.load()
        target_artifacts = self.target_provider.load()

        logger.info("Preprocessing artifacts")
        source_elements = self.source_preprocessor.preprocess(source_artifacts)
        target_elements = self.target_preprocessor.preprocess(target_artifacts)

        # Before embedding, so the summary is part of the vector rather than
        # something bolted on beside it.
        if self.source_summarizer:
            logger.info("Summarizing source elements")
            self.source_summarizer.summarize(source_elements)
        if self.target_summarizer:
            logger.info("Summarizing target elements")
            self.target_summarizer.summarize(target_elements)

        if self.reset_vector_stores:
            logger.info("Resetting vector stores")
            self.source_store.clear()
            self.target_store.clear()

        logger.info("Calculating embeddings")
        source_embeddings = self.embedder.create_embeddings(source_elements)
        target_embeddings = self.embedder.create_embeddings(target_elements)

        logger.info("Storing elements")
        self.source_store.replace_elements(source_elements, source_embeddings)
        self.target_store.replace_elements(target_elements, target_embeddings)

        logger.info("Classifying trace links")
        all_results: list[ClassificationResult] = []
        for source_element in source_elements:
            if not source_element.compare:
                continue
            source_embedding = self.embedder.create_embedding(source_element)
            candidates = self.target_store.find_similar_elements(source_embedding, self.n_results)
            results = self.classifier.classify(source_element, candidates)
            all_results.extend(results)

        if self.dependency_analyzer and self.dependency_expansion_depth > 0:
            logger.info("Expanding trace links with dependency graph")
            dependency_graph = self.dependency_analyzer.analyze(target_artifacts, target_elements)
            # No granularity floor: the expander already restricts itself to
            # compare=True nodes, and the aggregator rolls links up to the
            # requested output level afterwards.
            expander = DependencyLinkExpander(
                graph=dependency_graph,
                max_depth=self.dependency_expansion_depth,
            )
            all_results = expander.expand(all_results)

        logger.info("Aggregating results")
        trace_links = self.aggregator.aggregate(source_elements, target_elements, all_results)

        return TraceMatrix(
            source_elements,
            target_elements,
            trace_links,
            source_level=self.aggregator.source_level,
            target_level=self.aggregator.target_level,
        )
