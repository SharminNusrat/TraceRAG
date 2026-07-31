"""Declarative catalogue of what the pipeline can ingest and how.

This is the single place to edit when a new artifact type is supported: add an
entry here and the frontend picks up the new kind, its accepted file types, its
preprocessors and its output levels with no frontend change.
"""

from core.schemas import ElementLevel
from api.schemas import (
    AnalysisDefaults,
    AnalyzeRequest,
    ArtifactKindOption,
    CapabilitiesResponse,
    ClassifierOption,
    ClassifierType,
    OutputLevelOption,
    PreprocessorOption,
    PreprocessorType,
)

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 30 * 1024 * 1024

ROLE_SOURCE = "source"
ROLE_TARGET = "target"

# Human wording for each semantic level, reused across preprocessors.
LEVEL_LABELS: dict[ElementLevel, tuple[str, str]] = {
    ElementLevel.ARTIFACT: ("Whole document", "One link per document"),
    ElementLevel.SECTION: ("Section", "Report links on the numbered section"),
    ElementLevel.SENTENCE: ("Sentence", "Report links on the individual sentence"),
    ElementLevel.PACKAGE: ("Package", "Report links on the containing folder"),
    ElementLevel.FILE: ("File", "Report links on the whole source file"),
    ElementLevel.CLASS: ("Class", "Report links on the enclosing class"),
    ElementLevel.FUNCTION: ("Method / function", "Report links on the individual method"),
    ElementLevel.CHUNK: ("Code chunk", "Report links on the individual chunk"),
}


def _levels(*levels: ElementLevel) -> list[OutputLevelOption]:
    return [
        OutputLevelOption(key=level, label=LEVEL_LABELS[level][0], description=LEVEL_LABELS[level][1])
        for level in levels
    ]


# `tree` is intentionally absent: it exists to give the dependency analyzer a
# folder/file/class hierarchy, not to produce comparable chunks. `summarize`
# is also omitted - it needs a local LLM pass over every artifact.
REQUIREMENT_PREPROCESSORS = [
    PreprocessorOption(
        key=PreprocessorType.SECTION,
        label="Sections",
        description="Split on numbered headings (1., 1.1, …). Best for structured SRS documents.",
        output_levels=_levels(ElementLevel.SECTION, ElementLevel.ARTIFACT),
    ),
    PreprocessorOption(
        key=PreprocessorType.SENTENCE,
        label="Sentences",
        description="Split into individual sentences. Use when the document has no headings.",
        output_levels=_levels(ElementLevel.SENTENCE, ElementLevel.ARTIFACT),
    ),
    PreprocessorOption(
        key=PreprocessorType.SINGLE,
        label="Whole document",
        description="Treat the entire document as one requirement.",
        output_levels=_levels(ElementLevel.ARTIFACT),
    ),
]

CODE_PREPROCESSORS = [
    PreprocessorOption(
        key=PreprocessorType.METHOD,
        label="Methods",
        description="Parse into classes and methods with a syntax parser.",
        output_levels=_levels(ElementLevel.FUNCTION, ElementLevel.CLASS, ElementLevel.FILE),
    ),
    PreprocessorOption(
        key=PreprocessorType.LINE,
        label="Code chunks",
        description="Split into fixed-size overlapping chunks. Works for any language.",
        output_levels=_levels(ElementLevel.CHUNK, ElementLevel.FILE),
    ),
]

ARTIFACT_KINDS: list[ArtifactKindOption] = [
    ArtifactKindOption(
        key="requirements",
        label="Requirements",
        description="An SRS or requirements document.",
        extensions=[".txt", ".pdf", ".docx"],
        accepts_archive=False,
        accepts_folder=True,
        accepts_text=True,
        roles=[ROLE_SOURCE],
        preprocessors=REQUIREMENT_PREPROCESSORS,
        default_preprocessor=PreprocessorType.SECTION,
        default_output_level=ElementLevel.SECTION,
    ),
    ArtifactKindOption(
        key="code",
        label="Code",
        description="Source files, a folder, or a .zip archive.",
        extensions=[".py", ".js", ".ts", ".java"],
        accepts_archive=True,
        accepts_folder=True,
        accepts_text=False,
        roles=[ROLE_TARGET],
        preprocessors=CODE_PREPROCESSORS,
        default_preprocessor=PreprocessorType.METHOD,
        default_output_level=ElementLevel.FUNCTION,
    ),
]

CLASSIFIERS = [
    ClassifierOption(
        key=ClassifierType.REASONING,
        label="Reasoning classifier",
        description="Asks the model to justify each decision. Slower, more accurate.",
    ),
    ClassifierOption(
        key=ClassifierType.SIMPLE,
        label="Simple classifier",
        description="Single-shot yes/no scoring. Faster, less precise.",
    ),
]

ARTIFACT_KINDS_BY_KEY = {kind.key: kind for kind in ARTIFACT_KINDS}


def get_capabilities() -> CapabilitiesResponse:
    # Read straight off AnalyzeRequest so editing a default there reaches the UI.
    fields = AnalyzeRequest.model_fields
    return CapabilitiesResponse(
        artifact_kinds=ARTIFACT_KINDS,
        classifiers=CLASSIFIERS,
        defaults=AnalysisDefaults(
            classifier=fields["classifier"].default,
            n_results=fields["n_results"].default,
            dependency_expansion_depth=fields["dependency_expansion_depth"].default,
            analysis_mode=fields["analysis_mode"].default,
        ),
        max_upload_bytes=MAX_UPLOAD_BYTES,
        max_total_upload_bytes=MAX_TOTAL_UPLOAD_BYTES,
    )


def supported_extensions(kind_key: str) -> set[str]:
    kind = ARTIFACT_KINDS_BY_KEY.get(kind_key)
    return set(kind.extensions) if kind else set()
