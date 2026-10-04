"""Declarative catalogue of what the pipeline can ingest and how.

This is the single place to edit when a new artifact type is supported: add an
entry here and the frontend picks up the new kind, its accepted file types, its
preprocessors and its output levels with no frontend change.
"""

from core.projects.uploads import MAX_TOTAL_UPLOAD_BYTES, MAX_UPLOAD_BYTES
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


ROLE_SOURCE = "source"
ROLE_TARGET = "target"

# Kind keys, so the few places that must special-case one do not spell it out.
KIND_REQUIREMENTS = "requirements"
KIND_CODE = "code"
KIND_ARCHITECTURE = "architecture"
KIND_ARCHITECTURE_DOCUMENT = "architecture_document"

# Any artifact kind can sit on either side: the point is to link any two kinds,
# not requirements to code specifically. Kinds keep a `roles` list so a future
# one-directional type stays expressible.
BOTH_SIDES = [ROLE_SOURCE, ROLE_TARGET]

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
    ElementLevel.COMPONENT: ("Component", "Report links on the individual component"),
}


def _levels(*levels: ElementLevel) -> list[OutputLevelOption]:
    return [
        OutputLevelOption(key=level, label=LEVEL_LABELS[level][0], description=LEVEL_LABELS[level][1])
        for level in levels
    ]


# `tree` is intentionally absent: it exists to give the dependency analyzer a
# folder/file/class hierarchy, not to produce comparable chunks. `summarize`
# is also omitted - it needs a local LLM pass over every artifact. `section` is
# not offered either: documents arrive one item per file, so there are no
# numbered headings to split on. The preprocessor itself is still there, and
# saved runs that used it can still be re-run and updated.
REQUIREMENT_PREPROCESSORS = [
    PreprocessorOption(
        key=PreprocessorType.SENTENCE,
        label="Sentences",
        description="Split into individual sentences.",
        output_levels=_levels(ElementLevel.SENTENCE, ElementLevel.ARTIFACT),
    ),
    PreprocessorOption(
        key=PreprocessorType.SINGLE,
        label="Whole document",
        description="Treat each document as a whole.",
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

MODEL_PREPROCESSORS = [
    PreprocessorOption(
        key=PreprocessorType.MODEL_UML,
        label="Components",
        description="Split a UML model into its components. Interfaces become context on them.",
        output_levels=_levels(ElementLevel.COMPONENT, ElementLevel.ARTIFACT),
    ),
]

ARTIFACT_KINDS: list[ArtifactKindOption] = [
    ArtifactKindOption(
        key=KIND_REQUIREMENTS,
        label="Requirements",
        description="An SRS or requirements document.",
        extensions=[".txt", ".pdf", ".docx"],
        accepts_archive=False,
        accepts_folder=True,
        accepts_text=True,
        # Already prose: an LLM summary of a requirement restates it.
        summarize=False,
        roles=BOTH_SIDES,
        preprocessors=REQUIREMENT_PREPROCESSORS,
        default_preprocessor=PreprocessorType.SINGLE,
        default_output_level=ElementLevel.ARTIFACT,
    ),
    # Listed after requirements on purpose: both accept the same file types, and
    # a file is given the first kind that claims its extension, so a document
    # starts as requirements and is switched to this by hand.
    ArtifactKindOption(
        key=KIND_ARCHITECTURE_DOCUMENT,
        label="Architecture document",
        description="A document describing the system's architecture in prose.",
        extensions=[".txt", ".pdf", ".docx"],
        accepts_archive=False,
        accepts_folder=True,
        accepts_text=True,
        # Already prose, like requirements.
        summarize=False,
        roles=BOTH_SIDES,
        preprocessors=REQUIREMENT_PREPROCESSORS,
        default_preprocessor=PreprocessorType.SINGLE,
        default_output_level=ElementLevel.ARTIFACT,
    ),
    ArtifactKindOption(
        key=KIND_CODE,
        label="Code",
        description="Source files, a folder, or a .zip archive.",
        extensions=[".py", ".js", ".ts", ".java"],
        accepts_archive=True,
        accepts_folder=True,
        accepts_text=False,
        summarize=True,
        roles=BOTH_SIDES,
        preprocessors=CODE_PREPROCESSORS,
        default_preprocessor=PreprocessorType.METHOD,
        default_output_level=ElementLevel.FUNCTION,
    ),
    ArtifactKindOption(
        key=KIND_ARCHITECTURE,
        label="Architecture model",
        description="A UML model file (.uml or .xmi) describing components and interfaces.",
        # No .xml: it says nothing about the contents, so a folder holding a
        # model beside a pom.xml would ingest both.
        extensions=[".uml", ".xmi"],
        accepts_archive=False,
        accepts_folder=True,
        accepts_text=False,
        summarize=True,
        roles=BOTH_SIDES,
        preprocessors=MODEL_PREPROCESSORS,
        default_preprocessor=PreprocessorType.MODEL_UML,
        default_output_level=ElementLevel.COMPONENT,
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
            summarize_elements=fields["summarize_elements"].default,
            analysis_mode=fields["analysis_mode"].default,
        ),
        max_upload_bytes=MAX_UPLOAD_BYTES,
        max_total_upload_bytes=MAX_TOTAL_UPLOAD_BYTES,
    )
