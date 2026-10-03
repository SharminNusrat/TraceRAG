"""Connecting a project to the places its artifacts actually live.

Kept apart from the project routes: those are about what has been analysed,
these are about where the material comes from and how it is kept current.
"""

import logging
import shutil
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request,
    UploadFile, status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from api.capabilities import ARTIFACT_KINDS_BY_KEY, ROLE_SOURCE, ROLE_TARGET
from api.project_routes import config_of, update_graph
from api.routes import (
    UploadBudget,
    build_pipeline_response,
    build_provider,
    extract_archive,
    get_chroma_path,
    parse_json_field,
    relativize_response,
    safe_relative_path,
    save_upload,
)
from api.schemas import (
    AnalyzeResponse,
    GitHubConnectionResponse,
    GitHubSourceRequest,
    OAuthStartResponse,
    PairResponse,
    RepositoryLookupRequest,
    RepositoryOption,
    SourceRemovalResponse,
    SourceResponse,
    SourceStatusResponse,
    StagedUploadResponse,
    SyncConfigResult,
    SyncRequest,
    SyncResponse,
    SyncSourceResult,
    SyncStartResponse,
)
from core.auth import get_current_user
from core.db.models import Analysis, Project, ProjectConfig, User
from core.db.session import SessionLocal, get_db
from config import settings
from core import jobs, secrets
from core.secrets import SecretError
from core.git import (
    DEFAULT_RETURN,
    GitHubCredentialError,
    GitHubError,
    GitHubRepository,
    authorize_url,
    exchange_code,
    list_repositories,
    parse_repository,
    user_from_state,
    verify_token,
)
from core.projects import ORIGIN_GITHUB, artifact_store, service
from core.sync import (
    SyncError, check_project, fetch_source, forget_account_token, renames_since,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def require_project(db: Session, user: User, project_id: int) -> Project:
    project = service.get_project(db, user.user_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


def require_kind(kind: str) -> str:
    """Reject a kind the pipeline could not read, before anything is stored."""
    if kind not in ARTIFACT_KINDS_BY_KEY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown artifact kind '{kind}'.",
        )
    return kind


def oauth_redirect_uri(request: Request) -> str:
    """Where GitHub sends the browser back to. Must match the OAuth app exactly."""
    return str(request.url_for("github_oauth_callback"))


def user_github_token(user: User) -> str | None:
    """This user's own GitHub credential, if they have connected one."""
    return secrets.decrypt(user.github_token) if user.github_token else None


@router.get("/github/connection", response_model=GitHubConnectionResponse)
def github_connection(user: User = Depends(get_current_user)):
    """Whether this user's GitHub connection actually works.

    Asked of GitHub rather than answered from the database. A stored token says
    a connection was made once, not that it still stands - GitHub can end one
    without telling anybody, and reporting "connected" from the presence of a
    dead token is how someone ends up staring at a working-looking connection
    that fails every request.
    """
    if user.github_token:
        try:
            verify_token(secrets.decrypt(user.github_token))
        except GitHubCredentialError:
            forget_account_token(user)
        except (GitHubError, SecretError) as error:
            # GitHub unreachable, or the token unreadable. Neither says the
            # connection is over, so it is left alone and reported as it stands.
            logger.warning(f"Could not verify GitHub for user {user.user_id}: {error}")

    return GitHubConnectionResponse(
        connected=bool(user.github_token),
        login=user.github_login or None,
        configured=settings.github_oauth_configured,
    )


@router.get("/github/oauth/start", response_model=OAuthStartResponse)
def github_oauth_start(
    request: Request,
    # Where the browser should land afterwards. Sent by whichever screen asked,
    # so reconnecting from a project returns to that project rather than
    # stranding the user on a settings page they never meant to visit.
    return_to: str | None = Query(default=None),
    user: User = Depends(get_current_user),
):
    """Where to send the browser so this user can approve access."""
    if not secrets.available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Connecting GitHub needs secret_key set in backend/.env, so the "
                "token can be stored safely and read back after a restart."
            ),
        )
    try:
        return OAuthStartResponse(
            authorize_url=authorize_url(
                user.user_id, oauth_redirect_uri(request), return_to
            )
        )
    except GitHubError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get("/github/oauth/callback", name="github_oauth_callback")
def github_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Where GitHub sends the browser once the user has answered.

    Reached by a redirect, not by the app, so it cannot return JSON to anyone -
    it stores the result and sends the browser back where it came from, saying
    what happened in the address.
    """
    # Where to land. Only known once the state has been read, so a failure
    # before that falls back to the default rather than guessing.
    destination = DEFAULT_RETURN

    def home(**params) -> RedirectResponse:
        separator = "&" if "?" in destination else "?"
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}{destination}{separator}{urlencode(params)}"
        )

    if error_description:
        # The user pressed Cancel, or GitHub refused.
        return home(github="error", message=error_description)
    if not code or not state:
        return home(github="error", message="GitHub did not complete the sign-in.")

    try:
        # The state says whose account this belongs to, that we started it, and
        # where the person was when they did.
        user_id, destination = user_from_state(state)
        token, login = exchange_code(code, oauth_redirect_uri(request))
    except GitHubError as error:
        return home(github="error", message=str(error))

    user = db.get(User, user_id)
    if user is None:
        return home(github="error", message="That account no longer exists.")

    user.github_token = secrets.encrypt(token)
    user.github_login = login or None
    db.commit()

    logger.info(f"User {user_id} connected GitHub account '{login}'")
    return home(github="connected", login=login or "")


@router.delete("/github/connection", response_model=GitHubConnectionResponse)
def disconnect_github(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Forget this user's GitHub credential.

    Only removes our copy. Whether GitHub still lists TraceRAG as authorised is
    the user's to settle there, and the response says so.
    """
    user.github_token = None
    user.github_login = None
    db.commit()
    return GitHubConnectionResponse(
        connected=False, login=None, configured=settings.github_oauth_configured
    )


@router.get("/github/repositories", response_model=list[RepositoryOption])
def github_repositories(user: User = Depends(get_current_user)):
    """The repositories this user's connection can reach."""
    token = user_github_token(user)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your GitHub account first.",
        )
    try:
        return [RepositoryOption(**repo) for repo in list_repositories(token)]
    except GitHubError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.post("/github/branches", response_model=list[str])
def github_branches(
    request: RepositoryLookupRequest,
    user: User = Depends(get_current_user),
):
    """The branches of a repository, so one can be chosen before connecting."""
    try:
        return GitHubRepository(
            parse_repository(request.repository),
            # A token typed in for this one repository wins; otherwise whatever
            # this user connected their account with.
            token=request.token or user_github_token(user),
        ).branches()
    except GitHubError as error:
        # These messages are written to be read by whoever typed the name in.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


def to_source_response(source) -> SourceResponse:
    """A source as a client may see it, which is everything but the token."""
    return SourceResponse(
        source_id=source.source_id,
        kind=source.kind,
        name=source.name,
        origin=source.origin,
        location=source.location,
        branch=source.branch,
        last_sync_ref=source.last_sync_ref,
        last_synced_at=source.last_synced_at,
        is_active=source.is_active,
        has_token=bool(source.access_token),
    )


@router.get("/projects/{project_id}/sources", response_model=list[SourceResponse])
def list_sources(
    project_id: int,
    include_disconnected: bool = Query(
        default=False, description="Also list sources that are no longer synced."
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything this project holds - what a sync offers to refresh."""
    return [
        to_source_response(source)
        for source in service.list_sources(
            db, require_project(db, user, project_id), include_disconnected
        )
    ]


def require_pair_run(
    db: Session, project: Project, source_kind: str, target_kind: str
) -> Analysis:
    """The last run between two kinds, which is what a sync of them starts from.

    It says which kind sat on which side and holds the files a side nobody
    touched is restored from, so a pair that was never run cannot be synced.
    """
    latest = service.latest_pair_analysis(db, project, source_kind, target_kind)
    if latest is None or not latest.artifacts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This project has no saved run from {source_kind} to {target_kind} "
                f"with stored artifacts, so there is nothing to sync against. Run "
                f"and save an analysis first."
            ),
        )
    return latest


def behind_kinds(db: Session, project: Project, latest: Analysis) -> set[str]:
    """The kinds whose source has moved on since this pair's last run."""
    return {
        artifact.artifact_type
        for artifact in latest.artifacts
        if service.side_is_behind(db, project, artifact)
    }


@router.get("/projects/{project_id}/pairs", response_model=list[PairResponse])
def list_pairs(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every two kinds this project traces between - what a sync is offered for.

    Nothing is asked of GitHub here, so it is cheap enough to draw a page with.
    """
    project = require_project(db, user, project_id)

    pairs = []
    for source_kind, target_kind in service.list_pairs(db, project):
        latest = service.latest_pair_analysis(db, project, source_kind, target_kind)
        sides = [
            service.active_source_for_kind(db, project, kind)
            for kind in (source_kind, target_kind)
        ]
        pairs.append(PairResponse(
            source_kind=source_kind,
            target_kind=target_kind,
            source=to_source_response(sides[0]) if sides[0] else None,
            target=to_source_response(sides[1]) if sides[1] else None,
            out_of_date=bool(latest and behind_kinds(db, project, latest)),
        ))
    return pairs


@router.get(
    "/projects/{project_id}/sync/status",
    response_model=list[SourceStatusResponse],
)
def sync_status(
    project_id: int,
    source_kind: str = Query(description="The kind on the source side of the pair."),
    target_kind: str = Query(description="The kind on the target side of the pair."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What a sync of one pair would pick up, without fetching anything.

    One small request per connected source, so this is cheap enough to open a
    dialog with. A source that cannot be reached is reported with its reason
    rather than failing the whole answer.
    """
    project = require_project(db, user, project_id)
    behind = behind_kinds(
        db, project, require_pair_run(db, project, source_kind, target_kind)
    )
    return [
        SourceStatusResponse(
            source_id=status.source.source_id,
            kind=status.source.kind,
            name=status.source.name,
            origin=status.source.origin,
            location=status.source.location,
            branch=status.source.branch,
            last_sync_ref=status.source.last_sync_ref,
            latest_ref=status.latest_ref,
            changed=status.changed,
            checkable=status.source.origin == ORIGIN_GITHUB,
            error=status.error,
            needs_reconnect=status.needs_reconnect,
            behind=status.source.kind in behind,
        )
        for status in check_project(db, project)
        if status.source.kind in (source_kind, target_kind)
    ]


def write_staged_files(
    kind_key: str, files: list[UploadFile], paths: list, destination: Path
) -> None:
    """Write uploaded files into a directory, as this kind of artifact.

    Each file keeps the relative path the browser reported, so identifiers stay
    the ones the graph already holds - a requirement re-uploaded at the same
    path is recognised as that requirement rather than as a new one.
    """
    kind = ARTIFACT_KINDS_BY_KEY[kind_key]
    budget = UploadBudget()

    for index, upload in enumerate(files):
        raw_path = paths[index] if index < len(paths) else (upload.filename or "")
        relative = safe_relative_path(raw_path or upload.filename or "file")
        suffix = relative.suffix.lower()

        if suffix == ".zip" and kind.accepts_archive:
            archive = destination / f"__archive_{index}.zip"
            save_upload(upload, archive, budget)
            extract_archive(archive, destination, budget)
            continue

        if suffix not in set(kind.extensions):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{relative.name}' is not a supported {kind.label.lower()} file. "
                    f"Accepted: {', '.join(sorted(kind.extensions))}"
                    + (" or a .zip archive." if kind.accepts_archive else ".")
                ),
            )

        save_upload(upload, destination / relative, budget)


@router.post(
    "/projects/{project_id}/sources/{source_id}/files",
    response_model=StagedUploadResponse,
)
async def stage_source_files(
    project_id: int,
    source_id: int,
    files: list[UploadFile] = File(default=[]),
    file_paths: str = Form("[]"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Put a fresh copy of an uploaded source's files aside for the next sync.

    Nothing can go and fetch requirements: they sit on someone's machine, and
    the only way they change here is if that someone hands them over. So the
    files are staged first and named in the sync request afterwards, which
    keeps the sync itself a plain JSON call and lets the upload be redone
    without re-running anything.
    """
    project = require_project(db, user, project_id)
    source = service.get_source(db, project, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    if source.origin == ORIGIN_GITHUB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{source.name}' comes from a repository, so it is fetched "
                f"rather than uploaded. Use Sync now instead."
            ),
        )
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose at least one file.",
        )

    upload_id, staged = artifact_store.create_upload_dir()
    try:
        write_staged_files(source.kind, files, parse_json_field(file_paths, "file_paths", []), staged)
    except Exception:
        # Half a set of requirements is worse than none: a sync would read the
        # gap as files deleted and break every link that used them.
        artifact_store.discard_upload(upload_id)
        raise

    uploaded = {path for path, _ in artifact_store.collect_files(staged)}
    stored = stored_paths(db, project, source)
    matched = len(uploaded & stored)

    logger.info(
        f"Staged {len(uploaded)} file(s) for source {source_id} as upload "
        f"{upload_id}: {matched} of {len(stored)} known path(s) matched"
    )
    return StagedUploadResponse(
        source_id=source_id,
        upload_id=upload_id,
        file_count=len(uploaded),
        matched=matched,
        missing=len(stored - uploaded),
    )


def stored_paths(db: Session, project: Project, source) -> set[str]:
    """The paths this source's files were last stored at.

    Compared against what is being uploaded so the answer can say how much of
    it lines up. A path is an element's identity here, so files arriving under
    different ones are not the same requirements coming back - they are a new
    set, and every link into the old ones goes with them.
    """
    held = service.latest_artifact(db, source)
    if held is None:
        return set()
    return {file.relative_path for file in held.files}


def staged_dir(upload_id: str) -> Path:
    """The staged upload behind an id, or a refusal the user can act on."""
    directory = artifact_store.upload_dir(upload_id)
    if directory is None or not directory.is_dir():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "Those uploaded files are no longer waiting - staged uploads are "
                "cleared after a while. Upload them again."
            ),
        )
    return directory


def prepare_workspace(
    db: Session,
    project: Project,
    latest: Analysis,
    refreshed_ids: set[int],
    # What each connected source is at now, already read during the check, so
    # asking GitHub a second time is unnecessary.
    heads: dict[int, str],
    # Sources whose files were handed over rather than fetched, as
    # source_id -> staged upload id.
    replacements: dict[int, str],
) -> tuple[str, Path, list[dict], list[SyncSourceResult], dict[str, str]]:
    """Assemble the artifacts this sync will analyse.

    `latest` is the pair's last run. Both of its sides are filled, not only the
    one that moved: the pipeline compares two complete corpora, so a side
    nobody touched is restored from the blobs already stored rather than
    fetched again.

    Built as an ordinary pending upload, so saving it afterwards goes through
    exactly the path a hand-made upload does - blobs, artifact rows, source
    fingerprints and the sealed version all come for free.
    """
    upload_id, workspace = artifact_store.create_upload_dir()
    manifest: list[dict] = []
    reported: list[SyncSourceResult] = []
    # Files that moved, gathered across every refreshed source, so the graph
    # can follow its elements instead of declaring them lost.
    renames: dict[str, str] = {}

    for artifact in latest.artifacts:
        directory = workspace / artifact.role
        # Where this kind comes from *now*, which is not necessarily where the
        # last run got it: a project that uploaded its code and later connected
        # a repository should be fetched from the repository.
        source = service.active_source_for_kind(db, project, artifact.artifact_type)
        staged = replacements.get(source.source_id) if source else None
        refresh = source is not None and source.source_id in refreshed_ids

        if staged:
            # Handed over by hand. Takes precedence over restoring: these files
            # are the whole point of the sync.
            shutil.copytree(staged_dir(staged), directory, dirs_exist_ok=True)
            ref = None
        elif refresh:
            # Asked before fetching, while the source still remembers where it
            # was: afterwards its ref has moved on and the comparison is lost.
            head = heads.get(source.source_id)
            if head:
                renames.update(renames_since(source, head))
            ref = fetch_source(source, directory)
        else:
            # What the source holds now, which is not always what this pair
            # last read: a kind shared with another pair may have moved on
            # since, and this run is what catches this pair up with it.
            held = service.latest_artifact(db, source) if source else None
            entries = [(f.relative_path, f.sha256) for f in (held or artifact).files]
            if not artifact_store.materialise(entries, directory):
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=(
                        f"The stored files for '{artifact.name}' are no longer on "
                        f"disk, so this sync has nothing to compare against."
                    ),
                )
            ref = source.last_sync_ref if source else None

        manifest.append({
            "role": artifact.role,
            "artifact_type": artifact.artifact_type,
            "name": source.name if source else artifact.name,
            "directory": artifact.role,
            # Names the row this belongs to, so a fetched repository updates
            # its own source instead of being filed as a new upload.
            "source_id": source.source_id if source else None,
            "ref": ref,
        })
        if source is not None:
            reported.append(SyncSourceResult(
                source_id=source.source_id,
                name=source.name,
                kind=source.kind,
                origin=source.origin,
                refreshed=refresh or bool(staged),
                ref=ref,
            ))

    artifact_store.write_manifest(workspace, manifest)
    return upload_id, workspace, manifest, reported, renames


def run_config(
    db: Session,
    project: Project,
    config: ProjectConfig,
    manifest: list[dict],
    workspace: Path,
    version_id: int,
    note: str | None,
) -> tuple[Analysis, AnalyzeResponse]:
    """Run one configuration over the prepared workspace and save the result."""
    sides = {entry["role"]: entry for entry in manifest}
    directories = {role: workspace / entry["directory"] for role, entry in sides.items()}
    settings = config_of(config)

    started = time.perf_counter()
    result = build_pipeline_response(
        source_provider=build_provider(sides[ROLE_SOURCE]["artifact_type"],
                                       directories[ROLE_SOURCE]),
        target_provider=build_provider(sides[ROLE_TARGET]["artifact_type"],
                                       directories[ROLE_TARGET]),
        source_kind=sides[ROLE_SOURCE]["artifact_type"],
        target_kind=sides[ROLE_TARGET]["artifact_type"],
        source_preprocessor=settings.source_preprocessor,
        target_preprocessor=settings.target_preprocessor,
        classifier=settings.classifier,
        n_results=settings.n_results,
        source_output_level=settings.source_output_level,
        target_output_level=settings.target_output_level,
        dependency_expansion_depth=settings.dependency_expansion_depth,
        summarize_elements=settings.summarize_elements,
        chroma_path=get_chroma_path(f"project-{project.project_id}"),
        use_persistent_cache=True,
        # This configuration's own collections, holding the previous version's
        # elements. Replacing them is what makes the new state current.
        reset_vector_stores=False,
        # A sync is where the corpus changes, so it is the only place a link
        # can be pushed out of the top-k by code that has nothing to do with
        # it. Offering last run's links back means one can only end on a
        # verdict. Rebased onto this run's workspace first: they are stored as
        # project paths, and the pipeline names its elements absolutely.
        pinned_links=service.pinned_links(db, config.config_id),
        workspace_roots=list(directories.values()),
    )
    duration = time.perf_counter() - started
    result = relativize_response(result, list(directories.values()))

    analysis = service.save_analysis(
        db, project, note=note, config=settings, result=result,
        execution_duration=duration, version_id=version_id,
        source_kind=sides[ROLE_SOURCE]["artifact_type"],
        target_kind=sides[ROLE_TARGET]["artifact_type"],
    )
    return analysis, result


def summarise_config(
    db: Session,
    config: ProjectConfig,
    analysis: Analysis,
    previous: Analysis | None,
    result: AnalyzeResponse,
) -> SyncConfigResult:
    """What this configuration found, and how it differs from its last run.

    Compared against the same configuration only. Two configurations read the
    artifacts differently, so a diff between them would measure the reading
    rather than the change in the artifacts.
    """
    summary = SyncConfigResult(
        config_id=config.config_id,
        config_key=config.config_key,
        analysis_id=analysis.analysis_id,
        trace_links=len(result.trace_links),
    )
    if previous is None:
        return summary

    counts = service.compare_analyses(db, previous, analysis)["summary"]
    summary.added = counts["added"]
    summary.removed = counts["removed"]
    summary.modified = counts["modified"]
    summary.compared_with = previous.analysis_id
    return summary


def pair_configs(db: Session, project: Project, request: SyncRequest) -> list[ProjectConfig]:
    """The configurations a sync re-runs: the pair's own, narrowed if asked."""
    configs = [
        config for config in service.list_configs(db, project)
        if (config.source_kind, config.target_kind)
        == (request.source_kind, request.target_kind)
    ]
    if request.config_ids is not None:
        wanted = set(request.config_ids)
        configs = [config for config in configs if config.config_id in wanted]
    return configs


def perform_sync(
    db: Session,
    project: Project,
    request: SyncRequest,
    refreshed_ids: set[int],
    heads: dict[int, str],
    job_id: int | None = None,
) -> SyncResponse:
    """Do the work for one pair: fetch, re-run its configurations, update their graphs.

    Minutes long, so it is called from a background job rather than from the
    request. `job_id` is only for saying where it has got to.
    """
    def stage(text: str, current: int = 0, total: int = 0) -> None:
        if job_id is not None:
            jobs.set_stage(db, job_id, text, current, total)

    latest = service.latest_pair_analysis(
        db, project, request.source_kind, request.target_kind
    )
    configs = pair_configs(db, project, request)

    stage("Fetching sources")
    upload_id, workspace, manifest, sources, renames = prepare_workspace(
        db, project, latest, refreshed_ids, heads, request.replacements
    )

    version = service.next_version(db, project)
    results: list[SyncConfigResult] = []
    # The run that took the files. Everything after it shares the same blobs,
    # because every configuration analysed the very same bytes.
    holder: Analysis | None = None

    try:
        for number, config in enumerate(configs, start=1):
            stage(f"Analysing with configuration {number} of {len(configs)}", number, len(configs))
            previous = service.latest_analysis(db, project, config.config_id)
            try:
                analysis, result = run_config(
                    db, project, config, manifest, workspace,
                    version.version_id, request.note,
                )
            except Exception as error:
                # One configuration failing must not lose the others' work, or
                # the files this sync already fetched.
                logger.error(f"Sync of config {config.config_key} failed: {error}", exc_info=True)
                results.append(SyncConfigResult(
                    config_id=config.config_id, config_key=config.config_key,
                    error=str(error),
                ))
                continue

            if holder is None:
                service.claim_artifacts(db, analysis, upload_id)
                holder = analysis
            else:
                service.copy_artifacts(db, holder, analysis)

            update_graph(db, analysis, result, renames)
            results.append(summarise_config(db, config, analysis, previous, result))
    finally:
        # Nothing succeeded, so nothing claimed the files. Sources keep their
        # old refs and the next sync sees the same work still waiting.
        if holder is None:
            artifact_store.discard_upload(upload_id)
        else:
            # Their contents are stored as blobs now, and an id that could be
            # named again would replay files the project has already taken in.
            for staged in request.replacements.values():
                artifact_store.discard_upload(staged)

    return SyncResponse(
        synced=True,
        detail=(
            f"Version {version.version_number}: {len(refreshed_ids)} source(s) "
            f"refreshed, {len(results)} configuration(s) re-run."
        ),
        version_id=version.version_id,
        version_number=version.version_number,
        sources=sources,
        configs=results,
    )


def run_sync_job(
    job_id: int, project_id: int, user_id: int, request: SyncRequest,
    refreshed_ids: set[int], heads: dict[int, str],
) -> None:
    """The background half of a sync. Owns its own session.

    The request's session is closed by the time this runs - the response has
    already gone out - so nothing from it can be carried in here.
    """
    with SessionLocal() as db:
        jobs.start(db, job_id)
        try:
            project = service.get_project(db, user_id, project_id)
            if project is None:
                raise SyncError("The project was removed before the sync could run.")
            response = perform_sync(db, project, request, refreshed_ids, heads, job_id)
            jobs.succeed(db, job_id, response.model_dump(mode="json"))
            logger.info(f"Sync job {job_id} finished: {response.detail}")
        except Exception as error:
            logger.error(f"Sync job {job_id} failed: {error}", exc_info=True)
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            jobs.fail(db, job_id, str(detail))


@router.post("/projects/{project_id}/sync", response_model=SyncStartResponse)
def sync_project(
    project_id: int,
    request: SyncRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ask for one pair of a project to be brought up to date.

    Everything cheap happens here, so an answer that can be given now is given
    now: nothing to fetch, nothing saved to sync against, a sync already
    running. Only once there is real work does it become a job to watch.
    """
    project = require_project(db, user, project_id)

    running = jobs.active_job(db, project_id, jobs.KIND_SYNC)
    if running is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This project is already being synced by job {running.job_id}.",
        )

    latest = require_pair_run(db, project, request.source_kind, request.target_kind)

    configs = pair_configs(db, project, request)
    if not configs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This pair has no saved configuration to re-run.",
        )

    # Checked here rather than in the job: it is a handful of small requests,
    # and "nothing has changed" is an answer worth giving immediately instead
    # of through a job that does nothing.
    statuses = [
        s for s in check_project(db, project)
        if s.source.kind in (request.source_kind, request.target_kind)
    ]
    if request.source_ids is not None:
        wanted = set(request.source_ids)
        statuses = [s for s in statuses if s.source.source_id in wanted]

    refreshed_ids = {s.source.source_id for s in statuses if s.changed}
    # Where each source stands right now. Kept from the check so the fetch does
    # not have to ask GitHub the same question again.
    heads = {s.source.source_id: s.latest_ref for s in statuses if s.latest_ref}

    # A source whose files were handed over has changed by definition: nobody
    # uploads the requirements again to say nothing happened.
    for replaced_id, staged in request.replacements.items():
        replaced = service.get_source(db, project, replaced_id)
        if replaced is None or replaced.origin == ORIGIN_GITHUB:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Source {replaced_id} cannot have its files uploaded.",
            )
        staged_dir(staged)
        refreshed_ids.add(replaced_id)

    # Nothing to fetch can still leave work: a kind this pair shares with
    # another may have moved on, and re-running is what catches it up.
    behind = behind_kinds(db, project, latest)

    if not refreshed_ids and not behind and not request.force:
        return SyncStartResponse(
            started=False,
            detail="Everything is already up to date. Nothing was fetched.",
        )

    job = jobs.create_job(db, user.user_id, project_id, jobs.KIND_SYNC)
    background.add_task(
        run_sync_job, job.job_id, project_id, user.user_id, request, refreshed_ids, heads
    )
    logger.info(
        f"Sync job {job.job_id} filed for project {project_id}: "
        f"{len(refreshed_ids)} source(s) to refresh, {len(configs)} configuration(s)"
    )
    return SyncStartResponse(
        started=True,
        detail=(
            f"Syncing {len(refreshed_ids)} source(s) and re-running "
            f"{len(configs)} configuration(s)."
        ),
        job_id=job.job_id,
    )


@router.delete(
    "/projects/{project_id}/sources/{source_id}",
    response_model=SourceRemovalResponse,
)
def disconnect_source(
    project_id: int,
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stop syncing a source, without losing what past versions recorded."""
    project = require_project(db, user, project_id)
    source = service.get_source(db, project, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    name = source.name
    # Counted before the row is touched, so the answer describes what was kept.
    versions = service.versions_using(db, source)
    removed = service.disconnect_source(db, source)

    logger.info(
        f"Project {project_id} disconnected '{name}' "
        f"({'removed' if removed else f'kept, recorded in {versions} version(s)'})"
    )
    return SourceRemovalResponse(
        removed=removed,
        detail=(
            f"'{name}' was removed. No saved version had recorded it."
            if removed else
            f"'{name}' will no longer be synced. It is kept because "
            f"{versions} saved version{'' if versions == 1 else 's'} "
            f"record{'s' if versions == 1 else ''} what it was at the time."
        ),
    )


@router.post(
    "/projects/{project_id}/sources/{source_id}/reconnect",
    response_model=SourceResponse,
)
def reconnect_source(
    project_id: int,
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sync a disconnected source again, in place of whatever replaced it."""
    project = require_project(db, user, project_id)
    source = service.get_source(db, project, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    service.reconnect_source(db, project, source)
    logger.info(f"Project {project_id} reconnected '{source.name}'")
    return to_source_response(source)


@router.post(
    "/projects/{project_id}/sources/github",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def connect_github_source(
    project_id: int,
    request: GitHubSourceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Connect a repository as one of the project's artifact sets."""
    project = require_project(db, user, project_id)
    kind = require_kind(request.kind)

    if request.token and not secrets.available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "An access token cannot be stored until secret_key is set in "
                "backend/.env. Without it the encryption key changes on every "
                "restart and the token could not be read back."
            ),
        )

    try:
        repository = GitHubRepository(
            parse_repository(request.repository),
            # Checked with whatever will actually read it later, so a
            # repository that connects is one that can still be synced.
            token=request.token or user_github_token(user),
        )
        branch = request.branch or repository.default_branch()
        # Read the branch before storing anything, so a repository that cannot
        # be reached - or a token that cannot reach it - is refused now rather
        # than at the first sync.
        repository.head_commit(branch)
    except GitHubError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))

    source = service.connect_github_source(
        db,
        project,
        kind=kind,
        repository=repository.full_name,
        branch=branch,
        # The repository's own short name, unless the user gave it one.
        name=request.name or repository.full_name.split("/")[-1],
        token=request.token,
    )
    logger.info(
        f"Project {project_id} now takes its {kind} from "
        f"{source.location}@{source.branch}"
        f"{' (with its own token)' if source.access_token else ''}"
    )
    return to_source_response(source)
