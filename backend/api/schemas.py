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
    # The pairs the classifier judged, before they were rolled up to the output
    # level. Excluded from what goes over the wire: nobody viewing a run needs
    # them, they only exist so the next run can offer the same pairs again.
    element_links: list[tuple[str, str]] = Field(default_factory=list, exclude=True)


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
    # Which state of the artifacts this run analysed. Null for runs saved
    # before versioning existed. Shown so a run in this list can be matched to
    # a version in the history - otherwise the two lists describe the same
    # events with nothing in common to line them up by.
    version_number: int | None = None
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


# ----- Sources: what a project holds, and where it came from -----

class GitHubConnectionResponse(BaseModel):
    """Whether this user has connected their own GitHub account."""

    connected: bool
    # Their GitHub username, so the app can say whose account it is.
    login: str | None = None
    # False when the server has no OAuth app set up, in which case connecting
    # is not possible and a token has to be pasted per repository instead.
    configured: bool = False


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
    # A POST rather than a query string, because it carries a token and a
    # query string ends up in server logs and browser history.

    repository: str = Field(min_length=3, max_length=500)
    token: str | None = Field(default=None, max_length=500)


class GitHubSourceRequest(BaseModel):
    """Point one of a project's artifact sets at a repository."""

    # A key from the capabilities registry - "code", "requirements".
    kind: str = Field(min_length=1, max_length=50)
    # Its URL, or "owner/name". Whatever GitHub put in front of the user.
    repository: str = Field(min_length=3, max_length=500)
    # Omit to take whichever branch the repository itself defaults to.
    branch: str | None = Field(default=None, max_length=255)
    # Omit to name the source after the repository.
    name: str | None = Field(default=None, max_length=255)
    # Needed only for a repository the server's own token cannot read. Stored
    # encrypted, and never sent back - it can be replaced, not retrieved.
    token: str | None = Field(default=None, max_length=500)


class SourceResponse(BaseModel):
    """One artifact set the project holds, as the client sees it."""
    model_config = ConfigDict(from_attributes=True)

    source_id: int
    kind: str
    name: str
    origin: str
    location: str | None
    branch: str | None
    # Null until the first sync: connected, but nothing fetched yet.
    last_sync_ref: str | None
    last_synced_at: UtcDatetime | None
    is_active: bool
    # Whether a token of its own is held. The token itself is never returned.
    has_token: bool = False


class SourceStatusResponse(BaseModel):
    """Where one source stands against the place it comes from."""

    source_id: int
    kind: str
    name: str
    origin: str
    location: str | None
    branch: str | None
    # What was last taken in, and what is there now. Equal means nothing moved.
    last_sync_ref: str | None
    latest_ref: str | None
    changed: bool
    # An uploaded source cannot be checked from here; only whoever holds the
    # files knows whether they changed. The client offers a file picker for
    # these rather than a refresh.
    checkable: bool
    error: str | None = None
    # Whether granting GitHub access again is what fixes this. Lets the client
    # offer the one button that mends it, instead of describing the problem and
    # leaving the user to find the page.
    needs_reconnect: bool = False
    # Whether the project already holds newer files for this source than the
    # pair's last run read - another pair's sync, or a new upload, moved it on.
    behind: bool = False


class PairResponse(BaseModel):
    """Two artifact kinds this project traces between, and where they stand."""

    source_kind: str
    target_kind: str
    # Where each side comes from now. None when its source was disconnected.
    source: SourceResponse | None
    target: SourceResponse | None
    # Whether either side has moved on since this pair was last run.
    out_of_date: bool


class StagedUploadResponse(BaseModel):
    """Files put aside for a source, waiting for the sync that will use them."""

    source_id: int
    upload_id: str
    file_count: int
    # How many of these files sit at a path the source already holds. Nothing
    # is wrong with a zero - the whole set may have been reorganised - but it
    # means the sync will read every old file as gone, so it is worth saying.
    matched: int = 0
    # How many stored files nothing in this upload lands on.
    missing: int = 0


class SyncRequest(BaseModel):
    """Which pair of a project to bring up to date, and how much of it."""

    # The pair being synced: the two artifact kinds a trace runs between. A
    # project can hold several, and each is brought up to date on its own.
    source_kind: str
    target_kind: str
    # Omit to take every source of the pair that has moved. Naming some limits
    # it to those.
    source_ids: list[int] | None = None
    # Omit to re-run every configuration the pair has.
    config_ids: list[int] | None = None
    # Files supplied by hand for a source that cannot be fetched, as
    # source_id -> the id of a staged upload. This is how the requirements side
    # keeps up: it lives on someone's machine, so nothing can go and get it.
    replacements: dict[int, str] = Field(default_factory=dict)
    # Run even when nothing moved - for repeating a sync whose analysis failed.
    force: bool = False
    note: str | None = Field(default=None, max_length=200)


class SyncSourceResult(BaseModel):
    """What one source contributed to a sync."""

    source_id: int
    name: str
    kind: str
    origin: str
    # Whether this source was refreshed, or carried over from the last version.
    refreshed: bool
    ref: str | None


class SyncConfigResult(BaseModel):
    """What one configuration found after the artifacts moved."""

    config_id: int
    config_key: str
    analysis_id: int | None = None
    trace_links: int = 0
    # Against the same configuration's previous run, so the comparison is
    # between two states of the artifacts rather than two ways of reading them.
    added: int = 0
    removed: int = 0
    modified: int = 0
    compared_with: int | None = None
    error: str | None = None


class SyncResponse(BaseModel):
    """What a finished sync did. Read back from the job that ran it."""

    synced: bool
    detail: str
    version_id: int | None = None
    version_number: int | None = None
    sources: list[SyncSourceResult] = []
    configs: list[SyncConfigResult] = []


class SyncStartResponse(BaseModel):
    """The answer to asking for a sync, which is not the sync itself."""

    # False when there was nothing to do, and so no job was filed.
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
    # Present once it succeeded: exactly what the request would have returned
    # had it waited. Left untyped because its shape follows `kind` - a sync
    # report for one, a whole analysis for the other.
    result: dict | None = None
    error: str | None = None


class AnalysisStartResponse(BaseModel):
    """The answer to asking for an analysis, which is not the analysis."""

    job_id: int
    # Watches the job without an account. Job ids run in sequence, so holding
    # one is no proof of having started it.
    token: str
    # The files this run will be performed against, held server-side until the
    # result is saved. Returned now so a run can still be saved if the job is
    # watched from somewhere else.
    upload_id: str


class SourceRemovalResponse(BaseModel):
    """What disconnecting a source actually did to it."""

    # False when the row was kept: versions recorded this source, and that
    # record is the only thing saying the artifacts moved between them.
    removed: bool
    detail: str


# ----- Configurations, versions, and the graph they produce -----

class ProjectConfigResponse(BaseModel):
    """One way this project has been read."""
    model_config = ConfigDict(from_attributes=True)

    config_id: int
    config_key: str
    is_default: bool
    # The two kinds this configuration links. None for one saved without files.
    source_kind: str | None = None
    target_kind: str | None = None
    config: AnalysisConfig
    analysis_count: int
    created_at: UtcDatetime


class VersionSourceRef(BaseModel):
    """What one source was when a version was recorded."""

    source_id: int
    name: str
    kind: str
    origin: str
    ref: str


class ProjectVersionResponse(BaseModel):
    """One state of a project's artifacts."""

    version_id: int
    version_number: int
    note: str | None
    created_at: UtcDatetime
    analysis_count: int
    sources: list[VersionSourceRef] = []


class GraphSummary(BaseModel):
    """A configuration's graph in counts."""

    nodes_present: int = 0
    # Elements that were there and are not any more. Their links are what a
    # user is told about.
    nodes_gone: int = 0
    links_active: int = 0
    # Still recovered before, not recovered now, but both ends still exist.
    links_stale: int = 0
    # One end is gone. The only count that always means something is wrong.
    links_broken: int = 0


class GraphEdgeResponse(BaseModel):
    """One link, with both ends named."""

    edge_id: int
    from_kind: str
    from_identifier: str
    from_present: bool
    to_kind: str
    to_identifier: str
    to_present: bool
    confidence: float
    confidence_level: str
    explanation: str | None = None
    status: str


class GraphResponse(BaseModel):
    config_id: int
    config_key: str
    summary: GraphSummary
    # One page of links, most in need of a look first.
    links: list[GraphEdgeResponse] = []
    total: int = 0


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
