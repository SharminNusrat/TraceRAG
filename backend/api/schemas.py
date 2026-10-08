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


class AnalyzeRequest(BaseModel):
    source_preprocessor: PreprocessorType = PreprocessorType.SECTION
    target_preprocessor: PreprocessorType = PreprocessorType.METHOD
    classifier: ClassifierType = ClassifierType.REASONING
    n_results: int = 10
    source_output_level: ElementLevel | None = None
    target_output_level: ElementLevel | None = None
    dependency_expansion_depth: int = 1
    summarize_elements: bool = True
    analysis_mode: AnalysisMode = AnalysisMode.PROJECT
    project_id: str | None = None


class TraceLinkResponse(BaseModel):
    source_id: str
    source_content: str | None = None
    target_id: str
    target_content: str | None = None
    source_name: str | None = None
    target_name: str | None = None
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
    display_name: str | None = None


class AnalyzeResponse(BaseModel):
    trace_links: list[TraceLinkResponse]
    source_elements: list[ElementResponse] = []
    target_elements: list[ElementResponse] = []
    unimplemented: list[dict]
    summary: dict
    upload_id: str | None = None
    element_links: list[tuple[str, str]] | None = None


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
    note: str | None = Field(default=None, max_length=200)
    config: AnalysisConfig
    result: AnalyzeResponse
    execution_duration: float | None = None
    upload_id: str | None = None


class RerunRequest(BaseModel):
    """Run an analysis again over the same files, with the same settings."""
    note: str | None = Field(default=None, max_length=200)


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: int
    name: str
    artifact_type: str
    role: str
    file_count: int
    byte_size: int
    uploaded_at: UtcDatetime
    files_available: bool


class AnalysisSummaryResponse(BaseModel):
    """A run as it appears in a list - no links, no elements."""
    model_config = ConfigDict(from_attributes=True)

    analysis_id: int
    project_id: int
    # The analysis this is a run of.
    config_id: int
    note: str | None
    classifier_type: str
    top_k: int
    dependency_expansion_depth: int
    execution_duration: float | None
    created_at: UtcDatetime
    link_count: int
    artifact_count: int
    project_name: str
    version_number: int | None = None
    files_available: bool


class AnalysisDetailResponse(BaseModel):
    """A saved run reassembled into the shape the results view expects."""
    analysis_id: int
    project_id: int
    config_id: int
    version_number: int
    project_name: str
    note: str | None
    config: AnalysisConfig
    execution_duration: float | None
    created_at: UtcDatetime
    result: AnalyzeResponse
    artifacts: list[ArtifactResponse] = []


# ----- GitHub, and the sides of an analysis -----

class GitHubConnectionResponse(BaseModel):
    """Whether this user has connected their own GitHub account."""

    connected: bool
    login: str | None = None
    configured: bool = False
    needs_reconnect: bool = False


class OAuthStartResponse(BaseModel):
    """Where to send the browser so the user can approve access."""

    authorize_url: str


class RepositoryOption(BaseModel):
    """One repository a connected account can reach."""

    full_name: str
    private: bool = False
    default_branch: str = "main"
    description: str | None = None


class RepositoryLookupRequest(BaseModel):
    """Read a repository's branches before committing to connecting it."""

    repository: str = Field(min_length=3, max_length=500)
    token: str | None = Field(default=None, max_length=500)


class GitHubSideRequest(BaseModel):
    """Take the code side of an analysis from a repository."""

    repository: str = Field(min_length=3, max_length=500)
    branch: str | None = Field(default=None, max_length=255)
    token: str | None = Field(default=None, max_length=500)


class SourceResponse(BaseModel):
    """One side of an analysis, as the client sees it."""
    model_config = ConfigDict(from_attributes=True)

    source_id: int
    config_id: int
    # "source" or "target".
    role: str
    kind: str
    name: str
    origin: str
    location: str | None
    branch: str | None
    last_sync_ref: str | None
    has_token: bool = False
    files: list[str] = []


class SourceStatusResponse(BaseModel):
    """Where one side stands against the place it comes from."""

    source_id: int
    role: str
    kind: str
    name: str
    origin: str
    location: str | None
    branch: str | None
    last_sync_ref: str | None
    latest_ref: str | None
    changed: bool
    checkable: bool
    error: str | None = None
    needs_reconnect: bool = False


class MovedName(BaseModel):
    """Something that is called differently now: a renamed file, a moved element."""

    old: str
    new: str


class FileChangesResponse(BaseModel):
    """Which files of a side differ, by path and content hash."""

    added: list[str] = []
    removed: list[str] = []
    modified: list[str] = []
    renamed: list[MovedName] = []


class ElementChangesResponse(BaseModel):
    """Which of a side's compared elements differ, worked out from the files that changed."""

    added: list[str] = []
    removed: list[str] = []
    modified: list[str] = []
    moved: list[MovedName] = []


class SideChangesResponse(BaseModel):
    """What changed on one side between its stored files and the given ones."""

    role: str
    files: FileChangesResponse
    elements: ElementChangesResponse
    changed: bool
    meaningful: bool


class StagedUploadResponse(BaseModel):
    """Files put aside for one side, waiting for the update that will use them."""

    source_id: int
    upload_id: str
    file_count: int
    matched: int = 0
    missing: int = 0
    changes: SideChangesResponse | None = None


class SyncRequest(BaseModel):
    """Update one side of an analysis, and re-run the analysis over it."""

    source_id: int
    upload_id: str | None = None
    note: str | None = Field(default=None, max_length=200)


class SyncSourceResult(BaseModel):
    """What one side contributed to an update."""

    source_id: int
    role: str
    name: str
    kind: str
    origin: str
    refreshed: bool
    ref: str | None


class SyncResponse(BaseModel):
    """What a finished update did. Read back from the job that ran it."""

    synced: bool
    detail: str
    version_id: int | None = None
    version_number: int | None = None
    analysis_id: int | None = None
    trace_links: int = 0
    added: int = 0
    removed: int = 0
    compared_with: int | None = None
    sources: list[SyncSourceResult] = []
    changes: SideChangesResponse | None = None


class SyncStartResponse(BaseModel):
    """The answer to asking for an update, which is not the update itself."""

    started: bool
    detail: str
    job_id: int | None = None


class JobResponse(BaseModel):
    """A piece of background work, as it is being watched."""

    job_id: int
    kind: str
    state: str
    stage: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    result: dict | None = None
    error: str | None = None


class AnalysisStartResponse(BaseModel):
    """The answer to asking for an analysis, which is not the analysis."""

    job_id: int
    token: str
    upload_id: str


# ----- Analyses, their versions, and what changed between them -----

class ProjectConfigResponse(BaseModel):
    """One analysis: its two kinds, its settings, and where it stands."""
    model_config = ConfigDict(from_attributes=True)

    config_id: int
    project_id: int
    project_name: str
    source_kind: str | None = None
    target_kind: str | None = None
    config: AnalysisConfig
    analysis_count: int
    version_number: int | None = None
    latest_analysis_id: int | None = None
    latest_run_at: UtcDatetime | None = None
    link_count: int = 0
    sides: list[SourceResponse] = []
    created_at: UtcDatetime


class VersionSourceRef(BaseModel):
    """What one side was when a version was recorded."""

    source_id: int
    role: str
    name: str
    kind: str
    origin: str
    ref: str


class VersionRun(BaseModel):
    """One run of a version."""

    analysis_id: int
    note: str | None = None
    created_at: UtcDatetime
    link_count: int


class ProjectVersionResponse(BaseModel):
    """One state of an analysis's files."""

    version_id: int
    version_number: int
    note: str | None
    created_at: UtcDatetime
    analysis_count: int
    sources: list[VersionSourceRef] = []
    # What changed since the version before. Empty for version 1.
    changes: list[SideChangesResponse] = []
    runs: list[VersionRun] = []


class ReportLink(BaseModel):
    """One link in a change report: its state, and whether either end changed."""

    # "valid", "new", "no_longer_found" or "broken".
    state: str
    source_id: str
    target_id: str
    source_present: bool
    target_present: bool
    source_changed: bool
    target_changed: bool
    confidence: float
    confidence_level: str
    explanation: str | None = None
    source_content: str | None = None
    target_content: str | None = None
    source_name: str | None = None
    target_name: str | None = None


class ReportSummary(BaseModel):
    valid: int = 0
    no_longer_found: int = 0
    broken: int = 0
    new: int = 0
    uncovered: int = 0


class ReportVersion(BaseModel):
    """One version a change report spans, with what changed in its files."""

    version_number: int
    note: str | None = None
    created_at: UtcDatetime
    changes: list[SideChangesResponse] = []


class NetElement(BaseModel):
    """One element that changed between the two versions."""

    # "added", "removed", "modified", or "moved" to another file.
    change: str
    old: str | None = None
    new: str | None = None
    # A name a reader recognises: class and method, or a sentence's first words.
    label: str


class NetFile(BaseModel):
    """One file that changed between the two versions, with its elements."""

    path: str
    old_path: str | None = None
    # "added", "removed", "modified" or "renamed".
    change: str
    links: int = 0
    changed: int = 0
    elements: list[NetElement] = []


class NetCounts(BaseModel):
    files_added: int = 0
    files_removed: int = 0
    files_modified: int = 0
    files_renamed: int = 0
    elements_added: int = 0
    elements_removed: int = 0
    elements_modified: int = 0
    elements_moved: int = 0


class NetSide(BaseModel):
    """One side's net change between the two versions."""

    role: str
    whole_documents: bool
    counts: NetCounts
    files: list[NetFile] = []


class LineDiffResponse(BaseModel):
    """One file's text, earlier version against later, as unified diff lines."""

    lines: list[str] = []
    truncated: bool = False


class ChangeReportResponse(BaseModel):
    """What changed between two versions of one analysis."""

    config_id: int
    base_version: int | None = None
    head_version: int
    base_analysis_id: int | None = None
    head_analysis_id: int | None = None
    no_run_version: int | None = None
    summary: ReportSummary
    links: list[ReportLink] = []
    uncovered: list[str] = []
    uncovered_names: dict[str, str] = {}
    versions: list[ReportVersion] = []
    net: list[NetSide] = []
    net_available: bool = True


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    project_name: str
    description: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    analysis_count: int


class ProjectDetailResponse(ProjectResponse):
    # Every run of every analysis in it, newest first.
    analyses: list[AnalysisSummaryResponse] = []
