"""The two sides of an analysis: what each holds, and where its files come from."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api.capabilities import ARTIFACT_KINDS_BY_KEY, KIND_CODE
from api.github_routes import user_github_token
from api.project_routes import require_config, to_source_response
from api.sync_jobs import changes_response, side_changes
from api.uploads import parse_json_field
from api.schemas import GitHubSideRequest, SourceResponse, StagedUploadResponse
from core.auth import get_current_user
from core.db.models import ProjectConfig, ProjectSource, User
from core.db.session import get_db
from core import secrets
from core.git import GitHubError, GitHubRepository, parse_repository
from core.projects import artifact_store, service
from core.projects.uploads import UploadBudget, extract_archive, safe_relative_path, save_upload

router = APIRouter()
logger = logging.getLogger(__name__)


def require_side(db: Session, config: ProjectConfig, source_id: int) -> ProjectSource:
    side = service.get_side(db, config, source_id)
    if side is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Side not found.")
    return side


@router.get(
    "/projects/{project_id}/configs/{config_id}/sources",
    response_model=list[SourceResponse],
)
def list_sides(
    project_id: int,
    config_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The analysis's two sides, source first."""
    config = require_config(db, user, project_id, config_id)
    return [
        to_source_response(side, service.latest_artifact(db, side))
        for side in service.list_sides(db, config)
    ]


def write_staged_files(
    kind_key: str, files: list[UploadFile], paths: list, destination: Path
) -> None:
    """Write uploaded files into a directory, as this kind of artifact."""
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
    "/projects/{project_id}/configs/{config_id}/sources/{source_id}/files",
    response_model=StagedUploadResponse,
)
async def stage_side_files(
    project_id: int,
    config_id: int,
    source_id: int,
    files: list[UploadFile] = File(default=[]),
    file_paths: str = Form("[]"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Put a side's complete current file set aside for the update that uses it."""
    config = require_config(db, user, project_id, config_id)
    side = require_side(db, config, source_id)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose at least one file.",
        )

    upload_id, staged = artifact_store.create_upload_dir()
    try:
        write_staged_files(side.kind, files, parse_json_field(file_paths, "file_paths", []), staged)
    except Exception:
        artifact_store.discard_upload(upload_id)
        raise

    uploaded = {path for path, _ in artifact_store.collect_files(staged)}
    held = service.latest_artifact(db, side)
    stored = {file.relative_path for file in held.files} if held else set()
    matched = len(uploaded & stored)
    changes = side_changes(config, held, staged) if held else None

    logger.info(
        f"Staged {len(uploaded)} file(s) for side {source_id} as upload "
        f"{upload_id}: {matched} of {len(stored)} known path(s) matched"
    )
    return StagedUploadResponse(
        source_id=source_id,
        upload_id=upload_id,
        file_count=len(uploaded),
        matched=matched,
        missing=len(stored - uploaded),
        changes=changes_response(changes) if changes else None,
    )


@router.post(
    "/projects/{project_id}/configs/{config_id}/sources/{source_id}/github",
    response_model=SourceResponse,
)
def connect_github_side(
    project_id: int,
    config_id: int,
    source_id: int,
    request: GitHubSideRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Take the code side of an analysis from a GitHub repository."""
    config = require_config(db, user, project_id, config_id)
    side = require_side(db, config, source_id)
    if side.kind != KIND_CODE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a code side can be taken from GitHub. Upload the files instead.",
        )

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
            token=request.token or user_github_token(user),
        )
        branch = request.branch or repository.default_branch()
        repository.head_commit(branch)
    except GitHubError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))

    side = service.connect_github_side(
        db, side, repository=repository.full_name, branch=branch, token=request.token,
    )
    logger.info(
        f"Analysis {config_id} now takes its {side.role} side from "
        f"{side.location}@{side.branch}"
        f"{' (with its own token)' if side.access_token else ''}"
    )
    return to_source_response(side, service.latest_artifact(db, side))
