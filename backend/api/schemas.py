from datetime import datetime, timezone
from typing import Annotated
from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field
from enum import Enum

from core.schemas import ElementLevel


def as_utc(value: datetime) -> datetime:
    """Tag a naive timestamp as UTC."""
    # Without an offset a browser reads a timestamp as local time and shows
    # every saved analysis hours adrift. Every response timestamp goes here.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


class PreprocessorType(str, Enum):
    SINGLE = "single"
    SENTENCE = "sentence"
    SECTION = "section"
    SUMMARIZE = "summarize"
    LINE = "line"
    METHOD = "method"
    TREE = "tree"
    MODEL_UML = "model_uml"


class ClassifierType(str, Enum):
    SIMPLE = "simple"
    REASONING = "reasoning"


class AnalysisMode(str, Enum):
    SESSION = "session"
    PROJECT = "project"


class ArtifactInput(BaseModel):
    """One side of a trace: what kind of artifact, and where to read it from."""
    kind: str = "requirements"
    path: str = ""
    # Pasted instead of stored on disk. Only kinds with accepts_text allow it.
    text: str = ""


class AnalyzeRequest(BaseModel):
    # Symmetric on purpose: neither side is fixed to a kind, so linking code to
    # requirements, or a model to code, needs no change here.
    source: ArtifactInput = ArtifactInput(kind="requirements")
    target: ArtifactInput = ArtifactInput(kind="code")
    source_preprocessor: PreprocessorType = PreprocessorType.SECTION
    target_preprocessor: PreprocessorType = PreprocessorType.METHOD
    classifier: ClassifierType = ClassifierType.REASONING
    n_results: int = 10
    # How chunking happens is the *preprocessor*; these say at which level the
    # recovered links should be reported. None = leave links where they were found.
    source_output_level: ElementLevel | None = None
    target_output_level: ElementLevel | None = None
    dependency_expansion_depth: int = 1
    # Enriches elements with a one-sentence LLM summary before embedding, for
    # sides whose artifact kind is not prose. Off means no extra model calls.
    summarize_elements: bool = True
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


class ModelUnitsResponse(BaseModel):
    """An architecture component's relationships, for drawing the diagram."""
    name: str | None = None
    provides: list[str] = []
    requires: list[str] = []


class ElementResponse(BaseModel):
    identifier: str
    content: str
    level: str
    type: str
    parent_id: str | None = None
    model_units: ModelUnitsResponse | None = None


class AnalyzeResponse(BaseModel):
    trace_links: list[TraceLinkResponse]
    # Every element of each side, not just the linked ones, so the results view
    # can list both artifacts in full and highlight what a selection connects to.
    source_elements: list[ElementResponse] = []
    target_elements: list[ElementResponse] = []
    unimplemented: list[dict]
    summary: dict
    # Handle for the uploaded files, held server-side until someone saves the
    # run. Absent on the path-based endpoint, which analyses files it does not
    # own and therefore has nothing to keep.
    upload_id: str | None = None


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
    # Whether an LLM summary helps this kind. True for artifacts that are not
    # written in prose, where the raw text embeds poorly on its own.
    summarize: bool
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
    summarize_elements: bool
    analysis_mode: AnalysisMode


class CapabilitiesResponse(BaseModel):
    artifact_kinds: list[ArtifactKindOption]
    classifiers: list[ClassifierOption]
    defaults: AnalysisDefaults
    max_upload_bytes: int
    max_total_upload_bytes: int


PreprocessorOption.model_rebuild()


# ----- Auth -----

class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    # 72 bytes is bcrypt's ceiling; anything past it would be ignored, so it is
    # rejected here rather than silently accepted.
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """The account as the client sees it - never includes the password hash."""
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    full_name: str
    email: str
    created_at: UtcDatetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Returned alongside the token so registering or signing in takes one
    # round trip instead of an immediate follow-up call to /auth/me.
    user: UserResponse


# ----- Projects and saved analyses -----

class ProjectRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class AnalysisConfig(BaseModel):
    """The settings a run used, echoed back when a saved analysis is reopened."""
    source_preprocessor: PreprocessorType
    target_preprocessor: PreprocessorType
    source_output_level: ElementLevel | None = None
    target_output_level: ElementLevel | None = None
    classifier: ClassifierType
    n_results: int
    dependency_expansion_depth: int = 0
    # False by default: a caller that does not mention summarisation did not
    # use it, and claiming otherwise would misreport what the run actually did.
    summarize_elements: bool = False


class SaveAnalysisRequest(BaseModel):
    # What is different about this run. Runs are identified by when they ran,
    # so this is a remark, not a name.
    note: str | None = Field(default=None, max_length=200)
    config: AnalysisConfig
    # The pipeline's own response, handed straight back for storage. Reusing the
    # type means the client saves exactly what it was given, with no reshaping
    # in between to drift out of step.
    result: AnalyzeResponse
    execution_duration: float | None = None
    # Claims the uploaded files for this analysis. Omitted - or already claimed
    # by an earlier save - and the analysis is stored without its artifacts.
    upload_id: str | None = None


class RerunRequest(BaseModel):
    """Run a saved analysis again over the artifacts it already holds."""
    note: str | None = Field(default=None, max_length=200)
    # Omit to repeat the original settings; supply to answer "what would this
    # have found at class granularity, or with dependency expansion on?".
    config: AnalysisConfig | None = None


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: int
    name: str
    artifact_type: str
    role: str
    file_count: int
    byte_size: int
    uploaded_at: UtcDatetime
    # Whether the bytes are still on disk. The rows outlive the files, so a
    # stored artifact can be listed and still be impossible to hand back.
    files_available: bool


class AnalysisSummaryResponse(BaseModel):
    """An analysis as it appears in a list - no links, no elements."""
    model_config = ConfigDict(from_attributes=True)

    analysis_id: int
    project_id: int
    note: str | None
    classifier_type: str
    top_k: int
    dependency_expansion_depth: int
    execution_duration: float | None
    created_at: UtcDatetime
    link_count: int
    artifact_count: int
    project_name: str
    # False when the run kept artifacts but their bytes are gone, which is what
    # decides whether it can be re-run or downloaded.
    files_available: bool


class AnalysisDetailResponse(BaseModel):
    """A saved analysis reassembled into the shape the results view expects."""
    analysis_id: int
    project_id: int
    project_name: str
    note: str | None
    config: AnalysisConfig
    execution_duration: float | None
    created_at: UtcDatetime
    result: AnalyzeResponse
    artifacts: list[ArtifactResponse] = []


# ----- Comparing two runs -----

class ConfigDifference(BaseModel):
    setting: str
    base: str | None
    head: str | None


class ComparedLink(BaseModel):
    """A link that exists in only one of the two runs."""
    source_id: str
    target_id: str
    source_content: str | None = None
    target_content: str | None = None
    similarity_score: float
    confidence_level: str
    explanation: str | None = None


class ModifiedLink(BaseModel):
    """A link present in both runs whose score, band or reasoning moved."""
    source_id: str
    target_id: str
    source_content: str | None = None
    target_content: str | None = None
    changed_fields: list[str]
    base_similarity_score: float
    head_similarity_score: float
    base_confidence_level: str
    head_confidence_level: str
    base_explanation: str | None = None
    head_explanation: str | None = None


class ComparisonSummary(BaseModel):
    base_total: int
    head_total: int
    added: int
    removed: int
    modified: int
    unchanged: int


class ComparisonResponse(BaseModel):
    base: AnalysisSummaryResponse
    head: AnalysisSummaryResponse
    config_differences: list[ConfigDifference]
    # False when the two runs report at different granularities. The diff is
    # still returned, but every link will look added and removed, because the
    # identifiers refer to different things on each side.
    comparable: bool
    summary: ComparisonSummary
    added: list[ComparedLink]
    removed: list[ComparedLink]
    modified: list[ModifiedLink]
    # Coverage moving either way - usually the reason for re-running at all.
    newly_implemented: list[str]
    newly_unimplemented: list[str]


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    project_name: str
    description: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    analysis_count: int


class ProjectDetailResponse(ProjectResponse):
    analyses: list[AnalysisSummaryResponse] = []
