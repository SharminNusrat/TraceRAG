"""Asking a pair what has moved, and asking for it to be brought up to date."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.source_routes import require_project
from api.sync_jobs import (
    behind_kinds, pair_configs, require_pair_run, run_sync_job, staged_dir,
)
from api.schemas import SourceStatusResponse, SyncRequest, SyncStartResponse
from core.auth import get_current_user
from core.db.models import User
from core.db.session import get_db
from core import jobs
from core.projects import ORIGIN_GITHUB, service
from core.sync import check_project

router = APIRouter()
logger = logging.getLogger(__name__)


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
