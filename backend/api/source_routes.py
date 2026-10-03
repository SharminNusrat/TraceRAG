"""The artifact sets a project holds, where each comes from, and the pairs they form."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from api.capabilities import ARTIFACT_KINDS_BY_KEY
from api.github_routes import user_github_token
from api.sync_jobs import behind_kinds
from api.uploads import parse_json_field
from api.schemas import (
    GitHubSourceRequest,
    PairResponse,
    SourceRemovalResponse,
    SourceResponse,
    StagedUploadResponse,
)
from core.auth import get_current_user
from core.db.models import Project, User
from core.db.session import get_db
from core import secrets
from core.git import GitHubError, GitHubRepository, parse_repository
from core.projects import ORIGIN_GITHUB, artifact_store, service
from core.projects.uploads import UploadBudget, extract_archive, safe_relative_path, save_upload

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
