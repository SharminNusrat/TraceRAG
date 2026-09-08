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
    """How alike two vectors are, on the same scale retrieval reports.

    The vector collections are built with cosine distance and report
    `1 - distance`, so a score computed here sits on the same scale as one that
    came back from a query - which matters, because this number becomes the
    link's confidence.
    """
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
        # What each side holds, and how this run reads it. Together they name
        # the collections the elements are indexed in, so a project can hold
        # several configurations at once without them overwriting each other.
        source_kind: str,
        target_kind: str,
        config_key: str,
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
        # Links the last run made, as source identifier -> target identifiers.
        # Offered to the classifier again alongside whatever retrieval finds,
        # so a link can only end by being rejected, never by being crowded out
        # of the top-k as the corpus grows. Empty on a first run.
        pinned_links: dict[str, set[str]] | None = None,
        # The directories this run reads from. Only used to reduce element
        # identifiers to the form the pins are stored in, since the providers
        # disagree about whether they name elements absolutely.
        workspace_roots: list | None = None,
        # Called as the run moves through its steps, for anything watching from
        # outside the process. Given a stage in words, and where it has got to.
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
        source_collection, target_collection = collection_names(
            source_kind, target_kind, config_key
        )
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
        """Add last run's links to the candidates, if retrieval missed them.

        Retrieval returns a fixed number of nearest elements, not everything
        worth looking at, so a corpus that grew since the last run can push an
        existing link out of the list without anything having changed about
        either end. Putting it back means the classifier decides its fate.

        Skipped when the element is gone: there is nothing to ask about, and
        the link is broken rather than rejected.
        """
        pinned = self.pinned_links.get(self._stored_form(source_element.identifier))
        if not pinned:
            return candidates, 0

        already = {self._stored_form(element.identifier) for element, _ in candidates}
        carried = []
        for identifier in sorted(pinned - already):
            element = targets_by_id.get(identifier)
            vector = vectors_by_id.get(identifier)
            # compare=False elements are excluded from retrieval, so admitting
            # one here would put a candidate in front of the classifier that
            # this configuration says is not tracing material.
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

        # Checked up front rather than discovered in the totals. A pin map in a
        # different shape from this run's identifiers matches nothing and looks
        # exactly like having nothing to carry over, so the one number that
        # tells them apart is said out loud before any work is done.
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
