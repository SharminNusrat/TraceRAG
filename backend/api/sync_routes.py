"""Asking an analysis's sides what has moved, and updating one of them."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.project_routes import require_config
from api.source_routes import require_side
from api.sync_jobs import (
    held_files, no_change_detail, require_files, run_sync_job, side_changes, staged_dir,
)
from api.schemas import SourceStatusResponse, SyncRequest, SyncStartResponse
from core.auth import get_current_user
from core.db.models import User
from core.db.session import get_db
from core import jobs
from core.projects import ORIGIN_GITHUB
from core.sync import check_sides, check_source, last_ref

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/projects/{project_id}/configs/{config_id}/sync/status",
    response_model=list[SourceStatusResponse],
)
def sync_status(
    project_id: int,
    config_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Where each side of an analysis stands, without fetching anything.

    One small request per connected side, so this is cheap enough to open a
    dialog with. A side that cannot be reached is reported with its reason
    rather than failing the whole answer.
    """
    config = require_config(db, user, project_id, config_id)
    return [
        SourceStatusResponse(
            source_id=status.source.source_id,
            role=status.source.role,
            kind=status.source.kind,
            name=status.source.name,
            origin=status.source.origin,
            location=status.source.location,
            branch=status.source.branch,
            last_sync_ref=status.last_ref,
            latest_ref=status.latest_ref,
            changed=status.changed,
            checkable=status.source.origin == ORIGIN_GITHUB,
            error=status.error,
            needs_reconnect=status.needs_reconnect,
        )
        for status in check_sides(db, config)
    ]


@router.post(
    "/projects/{project_id}/configs/{config_id}/sync",
    response_model=SyncStartResponse,
)
def sync_side(
    project_id: int,
    config_id: int,
    request: SyncRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update one side of an analysis and re-run that analysis over it.

    Everything cheap happens here, so an answer that can be given now is given
    now: nothing to fetch, nothing saved to update, an update already running.
    Only once there is real work does it become a job to watch.
    """
    config = require_config(db, user, project_id, config_id)

    running = jobs.active_job(db, config_id, jobs.KIND_SYNC)
    if running is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This analysis is already being updated by job {running.job_id}.",
        )

    latest = require_files(db, config)
    side = require_side(db, config, request.source_id)

    head = None
    if request.upload_id:
        # Handed over by hand: checked now, so a lapsed upload is refused
        # before a job is filed for it - and compared now, because "nothing
        # has changed" is an answer worth giving immediately.
        changes = side_changes(config, held_files(latest, side), staged_dir(request.upload_id))
        detail = no_change_detail(changes)
        if detail:
            return SyncStartResponse(started=False, detail=detail)
    elif side.origin == ORIGIN_GITHUB:
        # Asked here rather than in the job: it is one small request, and
        # "nothing has changed" is an answer worth giving immediately.
        checked = check_source(side, last_ref(db, side))
        if checked.error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=checked.error)
        if not checked.changed:
            return SyncStartResponse(
                started=False,
                detail="Nothing has changed since this side was last fetched. Nothing was fetched.",
            )
        head = checked.latest_ref
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{side.name}' is uploaded, so nothing can fetch it. Upload its "
                f"current files to update it."
            ),
        )

    job = jobs.create_job(
        db, user.user_id, project_id, jobs.KIND_SYNC, config_id=config_id
    )
    background.add_task(run_sync_job, job.job_id, config_id, user.user_id, request, head)
    logger.info(f"Update job {job.job_id} filed for analysis {config_id}, side {side.role}")
    return SyncStartResponse(
        started=True,
        detail=f"Updating the {side.role} side and re-running the analysis.",
        job_id=job.job_id,
    )
