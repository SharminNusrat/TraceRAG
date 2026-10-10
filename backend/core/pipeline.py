import logging
from math import sqrt

from core.schemas import ElementLevel
from core.ingestion.base import ArtifactProvider
from core.preprocessing.base import Preprocessor
from core.embedding.base import EmbeddingCreator
from core.storage.chroma_store import ChromaStore, collection_names
from core.classification.base import Classifier, ClassificationResult
from core.dependency import CodeDependencyAnalyzer, DependencyLinkExpander
from core.summarization import ElementSummarizer
from core.output.result_aggregator import ResultAggregator
from core.content import relative_identifier
from core.output.formatter import TraceMatrix

logger = logging.getLogger(__name__)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """How alike two vectors are, on the same scale retrieval reports."""
    dot = sum(a * b for a, b in zip(left, right))
    size = sqrt(sum(a * a for a in left)) * sqrt(sum(b * b for b in right))
    return dot / size if size else 0.0


class TracePipeline:

    def __init__(
        self,
        source_provider: ArtifactProvider,
        target_provider: ArtifactProvider,
        source_preprocessor: Preprocessor,
        target_preprocessor: Preprocessor,
        embedder: EmbeddingCreator,
        classifier: Classifier,
        # What each side holds. They name the collections the elements are
        # indexed in.
        source_kind: str,
        target_kind: str,
        chroma_path: str = "./chroma_data",
        n_results: int = 10,
        source_output_level: ElementLevel | None = None,
        target_output_level: ElementLevel | None = None,
        dependency_analyzer: CodeDependencyAnalyzer | None = None,
        dependency_expansion_depth: int = 0,
        reset_vector_stores: bool = True,
        source_summarizer: ElementSummarizer | None = None,
        target_summarizer: ElementSummarizer | None = None,
        pinned_links: dict[str, set[str]] | None = None,
        # The directories this run reads from. Only used to reduce element
        # identifiers to the form the pins are stored in, since the providers
        # disagree about whether they name elements absolutely.
        workspace_roots: list | None = None,
        on_progress=None,
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
        self.pinned_links = pinned_links or {}
        self.workspace_roots = workspace_roots or []
        self.on_progress = on_progress
        self.aggregator = ResultAggregator(source_output_level, target_output_level)
        source_collection, target_collection = collection_names(source_kind, target_kind)
        logger.info(f"Indexing into {source_collection} and {target_collection}")
        self.source_store = ChromaStore(source_collection, chroma_path)
        self.target_store = ChromaStore(target_collection, chroma_path)

    def _stored_form(self, identifier: str) -> str:
        """An identifier as the pin store holds it, whichever provider made it."""
        return relative_identifier(identifier, self.workspace_roots)

    def _with_pinned_links(
        self,
        source_element,
        source_embedding: list[float],
        candidates: list[tuple],
        targets_by_id: dict,
        vectors_by_id: dict,
    ) -> tuple[list[tuple], int]:
        """Add last run's links to the candidates, if retrieval missed them."""
        pinned = self.pinned_links.get(self._stored_form(source_element.identifier))
        if not pinned:
            return candidates, 0

        already = {self._stored_form(element.identifier) for element, _ in candidates}
        carried = []
        for identifier in sorted(pinned - already):
            element = targets_by_id.get(identifier)
            vector = vectors_by_id.get(identifier)
            if element is None or vector is None or not element.compare:
                continue
            carried.append((element, cosine_similarity(source_embedding, vector)))

        return candidates + carried, len(carried)

    def _progress(self, stage: str, current: int = 0, total: int = 0) -> None:
        """Say where the run has got to, if anyone is listening."""
        if self.on_progress is None:
            return
        try:
            self.on_progress(stage, current, total)
        except Exception as error:
            # Reporting progress is not the work. A watcher that fails must not
            # take the analysis down with it.
            logger.warning(f"Could not report progress: {error}")

    def run(self) -> TraceMatrix:

        logger.info("Loading artifacts")
        self._progress("Loading artifacts")
        source_artifacts = self.source_provider.load()
        target_artifacts = self.target_provider.load()
        logger.info(
            f"Loaded {len(source_artifacts)} source and "
            f"{len(target_artifacts)} target artifact(s)"
        )

        logger.info("Preprocessing artifacts")
        self._progress("Splitting artifacts into elements")
        source_elements = self.source_preprocessor.preprocess(source_artifacts)
        target_elements = self.target_preprocessor.preprocess(target_artifacts)
        logger.info(
            f"Preprocessed into {len(source_elements)} source element(s) "
            f"({sum(1 for e in source_elements if e.compare)} compared) and "
            f"{len(target_elements)} target element(s) "
            f"({sum(1 for e in target_elements if e.compare)} compared)"
        )

        # Before embedding, so the summary is part of the vector rather than
        # something bolted on beside it.
        if self.source_summarizer:
            logger.info("Summarizing source elements")
            self._progress("Summarising elements")
            self.source_summarizer.summarize(source_elements)
        if self.target_summarizer:
            logger.info("Summarizing target elements")
            self.target_summarizer.summarize(target_elements)

        if self.reset_vector_stores:
            logger.info("Resetting vector stores")
            self.source_store.clear()
            self.target_store.clear()

        logger.info("Calculating embeddings")
        self._progress("Calculating embeddings")
        source_embeddings = self.embedder.create_embeddings(source_elements)
        target_embeddings = self.embedder.create_embeddings(target_elements)

        logger.info("Storing elements")
        self.source_store.replace_elements(source_elements, source_embeddings)
        self.target_store.replace_elements(target_elements, target_embeddings)

        comparable = [element for element in source_elements if element.compare]
        logger.info(
            f"Classifying {len(comparable)} source element(s), "
            f"top {self.n_results} candidates each"
        )
        # Both keyed by identifier, so a pinned link can be turned back into
        # the element and vector it was made from without a store round trip.
        targets_by_id = {self._stored_form(e.identifier): e for e in target_elements}
        vectors_by_id = {
            self._stored_form(e.identifier): vector
            for e, vector in zip(target_elements, target_embeddings)
        }

        if self.pinned_links:
            recognised = sum(
                1 for e in comparable if self._stored_form(e.identifier) in self.pinned_links
            )
            if recognised:
                logger.info(
                    f"{recognised}/{len(comparable)} source element(s) have links "
                    f"from the last run to re-offer"
                )
            else:
                logger.warning(
                    f"None of the {len(self.pinned_links)} pinned source identifier(s) "
                    f"match this run's elements, so no link can be carried over. They "
                    f"are probably stored in a different form than the run produces."
                )

        all_results: list[ClassificationResult] = []
        pinned_total = 0
        for number, source_element in enumerate(comparable, start=1):
            source_embedding = self.embedder.create_embedding(source_element)
            candidates = self.target_store.find_similar_elements(source_embedding, self.n_results)
            candidates, pinned = self._with_pinned_links(
                source_element, source_embedding, candidates, targets_by_id, vectors_by_id
            )
            pinned_total += pinned
            results = self.classifier.classify(source_element, candidates)
            logger.info(
                f"[{number}/{len(comparable)}] {source_element.identifier} -> "
                f"{len(results)} link(s) from {len(candidates)} candidate(s)"
                + (f", {pinned} carried over" if pinned else "")
            )
            self._progress("Recovering trace links", number, len(comparable))
            all_results.extend(results)

        # Taken before expansion on purpose. These are kept so the next run can
        # offer the same pairs to the classifier again, and the classifier only
        # ever chose these - the expanded ones below are derived from the call
        # graph and will be derived again from whatever survives.
        element_links = [(r.source.identifier, r.target.identifier) for r in all_results]
        if pinned_total:
            logger.info(f"Carried {pinned_total} link(s) from the last run into classification")

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
        self._progress("Collecting results")
        trace_links = self.aggregator.aggregate(source_elements, target_elements, all_results)

        return TraceMatrix(
            source_elements,
            target_elements,
            trace_links,
            source_level=self.aggregator.source_level,
            target_level=self.aggregator.target_level,
            element_links=element_links,
        )
