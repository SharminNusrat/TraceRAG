from pydantic import BaseModel
from enum import Enum

from core.schemas import ElementLevel


class SourceType(str, Enum):
    DOCUMENT = "document"
    TEXT = "text"


class PreprocessorType(str, Enum):
    SINGLE = "single"
    SENTENCE = "sentence"
    SECTION = "section"
    SUMMARIZE = "summarize"
    LINE = "line"
    METHOD = "method"
    TREE = "tree"


class ClassifierType(str, Enum):
    SIMPLE = "simple"
    REASONING = "reasoning"


class AnalysisMode(str, Enum):
    SESSION = "session"
    PROJECT = "project"


class AnalyzeRequest(BaseModel):
    source_type: SourceType = SourceType.DOCUMENT
    requirements_path: str = ""
    requirements_text: str = ""
    codebase_path: str
    source_preprocessor: PreprocessorType = PreprocessorType.SECTION
    target_preprocessor: PreprocessorType = PreprocessorType.METHOD
    classifier: ClassifierType = ClassifierType.REASONING
    n_results: int = 10
    # How chunking happens is the *preprocessor*; these say at which level the
    # recovered links should be reported. None = leave links where they were found.
    source_output_level: ElementLevel | None = None
    target_output_level: ElementLevel | None = None
    dependency_expansion_depth: int = 1
    analysis_mode: AnalysisMode = AnalysisMode.PROJECT
    project_id: str | None = None


class TraceLinkResponse(BaseModel):
    source_id: str
    source_content: str | None = None
    target_id: str
    target_content: str | None = None
    confidence: float
    confidence_level: str
    explanation: str | None = None


class AnalyzeResponse(BaseModel):
    trace_links: list[TraceLinkResponse]
    unimplemented: list[dict]
    summary: dict


# ----- Capabilities (drives the frontend's option lists) -----

class PreprocessorOption(BaseModel):
    key: PreprocessorType
    label: str
    description: str
    output_levels: list["OutputLevelOption"]


class OutputLevelOption(BaseModel):
    key: ElementLevel
    label: str
    description: str


class ArtifactKindOption(BaseModel):
    key: str
    label: str
    description: str
    extensions: list[str]
    accepts_archive: bool
    accepts_folder: bool
    accepts_text: bool
    roles: list[str]
    preprocessors: list[PreprocessorOption]
    default_preprocessor: PreprocessorType
    default_output_level: ElementLevel | None


class ClassifierOption(BaseModel):
    key: ClassifierType
    label: str
    description: str


class AnalysisDefaults(BaseModel):
    """Run defaults, mirrored from AnalyzeRequest so the UI never hardcodes them."""
    classifier: ClassifierType
    n_results: int
    dependency_expansion_depth: int
    analysis_mode: AnalysisMode


class CapabilitiesResponse(BaseModel):
    artifact_kinds: list[ArtifactKindOption]
    classifiers: list[ClassifierOption]
    defaults: AnalysisDefaults
    max_upload_bytes: int
    max_total_upload_bytes: int


PreprocessorOption.model_rebuild()
