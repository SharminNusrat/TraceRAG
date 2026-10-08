"""Watching work that is still running."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.schemas import JobResponse
from core.auth import get_current_user_optional
from core.db.models import User
from core.db.session import get_db
from core import jobs

router = APIRouter(tags=["jobs"])
logger = logging.getLogger(__name__)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    token: str | None = Query(
        default=None, description="Given when the job was started. Needed without an account."
    ),
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Where a job has got to, and its result once it is done."""
    job = jobs.get_job(db, job_id, user.user_id if user else None, token)
    if job is None:
        # Someone else's job is not theirs to find, so it reads as missing.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    stored = jobs.load_result(job)
    return JobResponse(
        job_id=job.job_id,
        kind=job.kind,
        state=job.state,
        stage=job.stage,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=stored,
        error=job.error,
    )
