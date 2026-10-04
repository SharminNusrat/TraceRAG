"""Turning a run's settings into a configured pipeline, and running it.

Every way of starting a run - an upload, a re-run, an update - ends here, so
the settings are read one way.
"""

from pathlib import Path

from fastapi import HTTPException

from api.capabilities import ARTIFACT_KINDS_BY_KEY
from api.schemas import (
    AnalyzeRequest, AnalyzeResponse, ArtifactInput, ClassifierType, ElementResponse,
    PreprocessorType, TraceLinkResponse,
)
from config import settings
from core.cache import PersistentSummaryCache
from core.classification import SimpleClassifier, ReasoningClassifier, OllamaChatProvider, GroqChatProvider
from core.dependency import CodeDependencyAnalyzer
from core.embedding import OllamaEmbeddingCreator
from core.ingestion import CodeProvider, DocumentProvider, ModelProvider, TextProvider
from core.pipeline import TracePipeline
from core.content import relative_identifier
from core.output.names import display_names, model_name
from core.preprocessing import ArtifactPreprocessor, SentencePreprocessor, SectionPreprocessor, SummarizePreprocessor, CodeChunkingPreprocessor, CodeMethodPreprocessor, CodeTreePreprocessor, ModelUmlPreprocessor
from core.projects.diff import Item, items_from_elements
from core.projects.run_config import expansion_depth
from core.schemas import ElementLevel
from core.summarization import ElementSummarizer


def request_default(field: str):
    """Default for a form field, taken from AnalyzeRequest.
    """
    return AnalyzeRequest.model_fields[field].default


def provider_for(side: ArtifactInput, role: str):
    """Provider for one side of a path-based request."""
    kind = ARTIFACT_KINDS_BY_KEY.get(side.kind)
    if kind is None:
        raise HTTPException(status_code=400, detail=f"Unknown artifact kind '{side.kind}'.")
    if role not in kind.roles:
        raise HTTPException(
            status_code=400,
            detail=f"'{kind.label}' artifacts cannot be the {role} of a trace.",
        )

    if side.text.strip():
        if not kind.accepts_text:
            raise HTTPException(
                status_code=400,
                detail=f"'{kind.label}' artifacts cannot be provided as pasted text.",
            )
        return TextProvider(side.text)

    if not side.path:
        raise HTTPException(
            status_code=400, detail=f"Give a path or text for the {role} artifact."
        )
    return build_provider(side.kind, side.path)


def build_provider(kind_key: str, path):
    """Reads a directory of artifacts of one kind.

    Extend this alongside the ARTIFACT_KINDS registry when adding a new type.
    """
    match kind_key:
        case "requirements" | "architecture_document":
            return DocumentProvider(str(path))
        case "code":
            return CodeProvider(str(path))
        case "architecture":
            return ModelProvider(str(path))

    raise HTTPException(status_code=400, detail=f"No provider registered for '{kind_key}'.")


def get_preprocessor(preprocessor_type: PreprocessorType):
    match preprocessor_type:
        case PreprocessorType.SINGLE:
            return ArtifactPreprocessor()
        case PreprocessorType.SENTENCE:
            return SentencePreprocessor()
        case PreprocessorType.SECTION:
            return SectionPreprocessor()
        case PreprocessorType.SUMMARIZE:
            return SummarizePreprocessor()
        case PreprocessorType.LINE:
            return CodeChunkingPreprocessor()
        case PreprocessorType.METHOD:
            return CodeMethodPreprocessor()
        case PreprocessorType.TREE:
            return CodeTreePreprocessor()
        case PreprocessorType.MODEL_UML:
            return ModelUmlPreprocessor()


def read_items(kind_key: str, preprocessor: PreprocessorType, directory: Path) -> list[Item]:
    """One side's elements, split the way a run splits them, without any model.

    What an update compares to say which elements changed. Identifiers are
    made relative to `directory`, the form every stored identifier takes.
    """
    if not any(path.is_file() for path in directory.rglob("*")):
        return []
    elements = get_preprocessor(preprocessor).preprocess(build_provider(kind_key, directory).load())
    roots = [directory]
    for element in elements:
        element.identifier = relative_identifier(element.identifier, roots)
        if element.parent_id:
            element.parent_id = relative_identifier(element.parent_id, roots)
    return items_from_elements(elements)


def name_elements(response: AnalyzeResponse) -> AnalyzeResponse:
    """Give each element, and each link's two ends, the name a person reads it by.

    Worked out from the elements a response already holds, so a stored run is
    named exactly as it was when it ran.
    """
    names = {}
    for elements in (response.source_elements, response.target_elements):
        names.update(display_names([
            (element.identifier, model_name(element.level, element.model_units))
            for element in elements
        ]))
    def shown(identifier: str) -> str | None:
        # Only a name that differs from the identifier is worth sending.
        return names.get(identifier) if names.get(identifier) != identifier else None

    for element in (*response.source_elements, *response.target_elements):
        element.display_name = shown(element.identifier)
    for link in response.trace_links:
        link.source_name = shown(link.source_id)
        link.target_name = shown(link.target_id)
    return response


def get_chat_provider():
    # provider = OllamaChatProvider()
    return GroqChatProvider(api_keys=settings.groq_api_keys_list)


def get_classifier(classifier_type: ClassifierType, use_cache: bool):
    provider = get_chat_provider()
    match classifier_type:
        case ClassifierType.SIMPLE:
            return SimpleClassifier(provider=provider, use_persistent_cache=use_cache)
        case ClassifierType.REASONING:
            return ReasoningClassifier(provider=provider, use_persistent_cache=use_cache)


def get_summarizer(kind_key: str, enabled: bool, use_cache: bool) -> ElementSummarizer | None:
    """A summarizer for one side, or None when that side needs no summaries.
    """
    kind = ARTIFACT_KINDS_BY_KEY.get(kind_key)
    if not enabled or kind is None or not kind.summarize:
        return None

    provider = get_chat_provider()
    # Keyed by the model, so switching models does not mix two different
    # descriptions of the same code into one index.
    cache = PersistentSummaryCache(provider.model_name()) if use_cache else None
    return ElementSummarizer(provider=provider, cache=cache)


def build_pipeline_response(
    source_provider,
    target_provider,
    source_kind: str,
    target_kind: str,
    source_preprocessor: PreprocessorType,
    target_preprocessor: PreprocessorType,
    classifier: ClassifierType,
    n_results: int,
    source_output_level: ElementLevel | None,
    target_output_level: ElementLevel | None,
    dependency_expansion_depth: int,
    chroma_path: str,
    use_persistent_cache: bool,
    reset_vector_stores: bool,
    summarize_elements: bool = request_default("summarize_elements"),
    # Last run's links for this analysis, so the classifier is asked about
    # them again rather than losing them to a shifted top-k.
    pinned_links: dict[str, set[str]] | None = None,
    # The directories the providers read from, so pinned identifiers and this
    # run's elements can be compared in the same form.
    workspace_roots: list[Path] | None = None,
    on_progress=None,
) -> AnalyzeResponse:
    dependency_expansion_depth = expansion_depth(target_kind, dependency_expansion_depth)

    pipeline = TracePipeline(
        source_provider=source_provider,
        target_provider=target_provider,
        source_preprocessor=get_preprocessor(source_preprocessor),
        target_preprocessor=get_preprocessor(target_preprocessor),
        source_kind=source_kind,
        target_kind=target_kind,
        # Only the sides whose artifacts are not prose; the rest get None.
        source_summarizer=get_summarizer(
            source_kind, summarize_elements, use_persistent_cache
        ),
        target_summarizer=get_summarizer(
            target_kind, summarize_elements, use_persistent_cache
        ),
        embedder=OllamaEmbeddingCreator(use_persistent_cache=use_persistent_cache),
        classifier=get_classifier(classifier, use_persistent_cache),
        chroma_path=chroma_path,
        n_results=n_results,
        source_output_level=source_output_level,
        target_output_level=target_output_level,
        # Already 0 for anything but a code target, which has no call graph
        # for an analyzer to walk.
        dependency_analyzer=(
            CodeDependencyAnalyzer() if dependency_expansion_depth > 0 else None
        ),
        dependency_expansion_depth=dependency_expansion_depth,
        reset_vector_stores=reset_vector_stores,
        pinned_links=pinned_links,
        workspace_roots=workspace_roots,
        on_progress=on_progress,
    )
    result = pipeline.run().to_dict()

    return name_elements(AnalyzeResponse(
        trace_links=[TraceLinkResponse(**link) for link in result["trace_links"]],
        source_elements=[ElementResponse(**e) for e in result["source_elements"]],
        target_elements=[ElementResponse(**e) for e in result["target_elements"]],
        unimplemented=result["unimplemented"],
        summary=result["summary"],
        element_links=result["element_links"],
    ))
