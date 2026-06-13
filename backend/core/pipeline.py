import logging
from core.ingestion.base import ArtifactProvider
from core.preprocessing.base import Preprocessor
from core.embedding.base import EmbeddingCreator
from core.storage.chroma_store import ChromaStore, SOURCE_COLLECTION, TARGET_COLLECTION
from core.classification.base import Classifier, ClassificationResult
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
        source_granularity: int = 0,
        target_granularity: int = 0,
    ):
        self.source_provider = source_provider
        self.target_provider = target_provider
        self.source_preprocessor = source_preprocessor
        self.target_preprocessor = target_preprocessor
        self.embedder = embedder
        self.classifier = classifier
        self.n_results = n_results
        self.aggregator = ResultAggregator(source_granularity, target_granularity)
        self.source_store = ChromaStore(SOURCE_COLLECTION, chroma_path)
        self.target_store = ChromaStore(TARGET_COLLECTION, chroma_path)

    def run(self) -> TraceMatrix:

        logger.info("Loading artifacts")
        source_artifacts = self.source_provider.load()
        target_artifacts = self.target_provider.load()

        logger.info("Preprocessing artifacts")
        source_elements = self.source_preprocessor.preprocess(source_artifacts)
        target_elements = self.target_preprocessor.preprocess(target_artifacts)

        logger.info("Calculating embeddings")
        source_embeddings = self.embedder.create_embeddings(source_elements)
        target_embeddings = self.embedder.create_embeddings(target_elements)

        logger.info("Storing elements")
        self.source_store.add_elements(source_elements, source_embeddings)
        self.target_store.add_elements(target_elements, target_embeddings)

        logger.info("Classifying trace links")
        all_results: list[ClassificationResult] = []
        for source_element in source_elements:
            if not source_element.compare:
                continue
            source_embedding = self.embedder.create_embedding(source_element)
            candidates = self.target_store.find_similar_elements(source_embedding, self.n_results)
            results = self.classifier.classify(source_element, candidates)
            all_results.extend(results)

        logger.info("Aggregating results")
        trace_links = self.aggregator.aggregate(source_elements, target_elements, all_results)

        return TraceMatrix(source_elements, target_elements, trace_links)