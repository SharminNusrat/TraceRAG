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
    # What each end is called where a person reads it: a UML component's
    # name. Absent when the identifier is the name.
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
    # What the element is called where a person reads it: a UML component's
    # name. Absent when the identifier is the name.
    display_name: str | None = None


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
    # The pairs the classifier confirmed, before they were rolled up to the
    # output level. Nobody viewing a run needs them, but they travel with the
    # result all the same: a new analysis is saved by the client posting this
    # back, and the save is what keeps them for the next update to offer again.
    # None rather than empty when a result does not carry them - an older
    # result, or another client - so that saving it leaves the stored pairs
    # alone instead of reading "nothing was said" as "nothing was linked".
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
    """Run an analysis again over the same files, with the same settings."""
    # Nothing else can be changed: other settings are another analysis, and
    # other files are an update.
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
    # Whether the bytes are still on disk. The rows outlive the files, so a
    # stored artifact can be listed and still be impossible to hand back.
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
    # Which state of its analysis's files this run read. Shown so a run in
    # this list can be matched to a version in the history.
    version_number: int | None = None
    # False when the run kept artifacts but their bytes are gone, which is what
    # decides whether it can be re-run or downloaded.
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
    # Their GitHub username, so the app can say whose account it is.
    login: str | None = None
    # False when the server has no OAuth app set up, in which case connecting
    # is not possible and a token has to be pasted per repository instead.
    configured: bool = False
    # True when a connection was stored but GitHub has just rejected it, so
    # it was dropped: what the user needs is to connect again, not for the
    # first time.
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
    # A POST rather than a query string, because it carries a token and a
    # query string ends up in server logs and browser history.

    repository: str = Field(min_length=3, max_length=500)
    token: str | None = Field(default=None, max_length=500)


class GitHubSideRequest(BaseModel):
    """Take the code side of an analysis from a repository."""

    # Its URL, or "owner/name". Whatever GitHub put in front of the user.
    repository: str = Field(min_length=3, max_length=500)
    # Omit to take whichever branch the repository itself defaults to.
    branch: str | None = Field(default=None, max_length=255)
    # Needed only for a repository the user's own connection cannot read.
    # Stored encrypted, and never sent back - it can be replaced, not read.
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
    # What the side's files are at the newest version: the commit they were
    # fetched at, or a fingerprint of an upload.
    last_sync_ref: str | None
    # Whether a token of its own is held. The token itself is never returned.
    has_token: bool = False
    # The side's current files - its file set at the newest version - by
    # relative path. Only filled in when one analysis is asked for.
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
    # By the identifier each one has now.
    modified: list[str] = []
    # Unchanged, but called something else: a sentence that slid down when
    # another was inserted above it, or an element of a renamed file.
    moved: list[MovedName] = []


class SideChangesResponse(BaseModel):
    """What changed on one side between its stored files and the given ones."""

    role: str
    files: FileChangesResponse
    elements: ElementChangesResponse
    # Whether any file differs at all.
    changed: bool
    # Whether any element the analysis compares differs. A whitespace-only
    # change is changed but not meaningful, and makes no new version.
    meaningful: bool


class StagedUploadResponse(BaseModel):
    """Files put aside for one side, waiting for the update that will use them."""

    source_id: int
    upload_id: str
    file_count: int
    # How many of these files sit at a path the side already holds. Nothing
    # is wrong with a zero - the whole set may have been reorganised - but it
    # means the update will read every old file as gone, so it is worth saying.
    matched: int = 0
    # How many stored files nothing in this upload lands on.
    missing: int = 0
    # What updating with these files would change, worked out before anything
    # is run.
    changes: SideChangesResponse | None = None


class SyncRequest(BaseModel):
    """Update one side of an analysis, and re-run the analysis over it."""

    # The side being updated.
    source_id: int
    # The complete current file set, staged beforehand. Required for an
    # uploaded side - nothing can go and fetch it. Left out for a side taken
    # from GitHub, which is fetched instead.
    upload_id: str | None = None
    note: str | None = Field(default=None, max_length=200)


class SyncSourceResult(BaseModel):
    """What one side contributed to an update."""

    source_id: int
    role: str
    name: str
    kind: str
    origin: str
    # Whether this side was refreshed, or carried over from the last version.
    refreshed: bool
    ref: str | None


class SyncResponse(BaseModel):
    """What a finished update did. Read back from the job that ran it."""

    synced: bool
    detail: str
    version_id: int | None = None
    version_number: int | None = None
    # The run the update made, and how its links differ from the run before.
    analysis_id: int | None = None
    trace_links: int = 0
    # Links the run found that the run before did not, and links it no
    # longer found or that broke.
    added: int = 0
    removed: int = 0
    compared_with: int | None = None
    sources: list[SyncSourceResult] = []
    # What changed on the updated side. Present even when nothing meaningful
    # did, which is when no version is made.
    changes: SideChangesResponse | None = None


class SyncStartResponse(BaseModel):
    """The answer to asking for an update, which is not the update itself."""

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
    # had it waited. Left untyped because its shape follows `kind` - an update
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


# ----- Analyses, their versions, and what changed between them -----

class ProjectConfigResponse(BaseModel):
    """One analysis: its two kinds, its settings, and where it stands."""
    model_config = ConfigDict(from_attributes=True)

    config_id: int
    project_id: int
    project_name: str
    # The two kinds this analysis links. None for one saved without files.
    source_kind: str | None = None
    target_kind: str | None = None
    config: AnalysisConfig
    # How many runs it has, across all its versions.
    analysis_count: int
    # Its newest version, and the newest run in it - what opening it shows.
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
    # Its runs, newest first: the first, and any re-run of the same files.
    runs: list[VersionRun] = []


class ReportLink(BaseModel):
    """One link in a change report: its state, and whether either end changed."""

    # "valid", "new", "no_longer_found" or "broken".
    state: str
    # Named as the later version names them, where the element still exists.
    source_id: str
    target_id: str
    source_present: bool
    target_present: bool
    # Whether the element at each end is new, edited or gone. Neither, for a
    # link that moved, means the files are not why: the classifier, or a
    # top-k that shifted, is.
    source_changed: bool
    target_changed: bool
    confidence: float
    confidence_level: str
    explanation: str | None = None
    source_content: str | None = None
    target_content: str | None = None
    # What each end is called where a person reads it, as the run named it.
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
    # Its identifier before and after; one is null when it was added or removed.
    old: str | None = None
    new: str | None = None
    # A name a reader recognises: class and method, or a sentence's first words.
    label: str


class NetFile(BaseModel):
    """One file that changed between the two versions, with its elements."""

    path: str
    # Its earlier path, when it was renamed.
    old_path: str | None = None
    # "added", "removed", "modified" or "renamed".
    change: str
    # How many of the report's links have their end on this side in this file,
    # and how many of those say that end changed.
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
    # Every element that moved - most only changed position in their file.
    elements_moved: int = 0


class NetSide(BaseModel):
    """One side's net change between the two versions."""

    role: str
    # Whether this side's elements are whole files, so its element list would
    # only repeat its files.
    whole_documents: bool
    counts: NetCounts
    files: list[NetFile] = []


class LineDiffResponse(BaseModel):
    """One file's text, earlier version against later, as unified diff lines."""

    lines: list[str] = []
    # True when the diff was longer than it is allowed to be, and cut short.
    truncated: bool = False


class ChangeReportResponse(BaseModel):
    """What changed between two versions of one analysis."""

    config_id: int
    # None when the later version is the first: there is nothing before it.
    base_version: int | None = None
    head_version: int
    # The runs each version was read through - its newest.
    base_analysis_id: int | None = None
    head_analysis_id: int | None = None
    # A version of the two that has no run. Its links cannot be compared, so
    # the later version's links are given as they are, or none if it is the
    # later one.
    no_run_version: int | None = None
    summary: ReportSummary
    links: list[ReportLink] = []
    # Source elements the later version links to nothing.
    uncovered: list[str] = []
    # What those of them that have a name of their own are called.
    uncovered_names: dict[str, str] = {}
    # The versions after the earlier one, up to the later one, each with what
    # it recorded: the step by step view.
    versions: list[ReportVersion] = []
    # The net change from the earlier version to the later one, side by side.
    net: list[NetSide] = []
    # False when a stored file of either version is gone, so no net change
    # could be worked out; the versions' own lists above still stand.
    net_available: bool = True


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    project_name: str
    description: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    # How many analyses it holds - not runs: an analysis may have many.
    analysis_count: int


class ProjectDetailResponse(ProjectResponse):
    # Every run of every analysis in it, newest first.
    analyses: list[AnalysisSummaryResponse] = []
