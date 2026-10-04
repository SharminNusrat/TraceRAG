"""Filing, watching and finishing background work.

Deliberately a table and nothing more. A queue would add a broker to install
and a worker to keep alive; one process running one job per project needs
neither, and the row is what the client watches either way.
"""

import hmac
import json
import logging
import secrets

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.db.models import Job, utcnow

logger = logging.getLogger(__name__)

# Filed but not started, running, and the two ways it can end.
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

UNFINISHED = (QUEUED, RUNNING)

KIND_SYNC = "sync"
KIND_ANALYSIS = "analysis"


def create_job(
    db: Session,
    user_id: int | None,
    project_id: int | None,
    kind: str,
    config_id: int | None = None,
) -> Job:
    job = Job(
        user_id=user_id,
        project_id=project_id,
        config_id=config_id,
        kind=kind,
        state=QUEUED,
        # Unguessable, because the id beside it is not.
        token=secrets.token_urlsafe(24),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def active_job(db: Session, config_id: int, kind: str) -> Job | None:
    """The job already working on this analysis, if there is one.

    Two updates of one analysis would fetch into separate workspaces and then
    write the same graph over each other, so the second is refused rather
    than allowed to race the first.
    """
    return db.execute(
        select(Job).where(
            Job.config_id == config_id,
            Job.kind == kind,
            Job.state.in_(UNFINISHED),
        ).order_by(Job.job_id.desc()).limit(1)
    ).scalar_one_or_none()


def get_job(db: Session, job_id: int, user_id: int | None, token: str | None) -> Job | None:
    """A job, for someone entitled to see it.

    Either its owner, or whoever holds the token it was created with - which is
    how a run started without an account is followed to the end.
    """
    job = db.get(Job, job_id)
    if job is None:
        return None

    owned = user_id is not None and job.user_id == user_id
    # compare_digest rather than ==: the comparison is against a secret, and
    # one that stops at the first wrong character leaks where it stopped.
    presented = token is not None and hmac.compare_digest(job.token, token)
    return job if owned or presented else None


def start(db: Session, job_id: int) -> None:
    _set(db, job_id, state=RUNNING, started_at=utcnow())


def set_stage(db: Session, job_id: int, stage: str, current: int = 0, total: int = 0) -> None:
    """Say what is happening now, for something to show while the wait passes."""
    _set(db, job_id, stage=stage, progress_current=current, progress_total=total)


def succeed(db: Session, job_id: int, result: dict) -> None:
    _set(
        db, job_id,
        state=SUCCEEDED,
        stage=None,
        result_json=json.dumps(result),
        finished_at=utcnow(),
    )


def fail(db: Session, job_id: int, error: str) -> None:
    _set(db, job_id, state=FAILED, stage=None, error=error, finished_at=utcnow())


def load_result(job: Job) -> dict | None:
    if not job.result_json:
        return None
    try:
        return json.loads(job.result_json)
    except ValueError:
        logger.warning(f"Job {job.job_id} stored a result that cannot be read back")
        return None


def sweep_unfinished(db: Session) -> int:
    """Fail whatever was still running when the process last stopped.

    Nothing survives a restart, so a job left saying "running" is describing a
    process that no longer exists. Left alone it would block its analysis's next
    update for ever, since that is exactly what an unfinished job is meant to do.
    """
    result = db.execute(
        update(Job)
        .where(Job.state.in_(UNFINISHED))
        .values(
            state=FAILED,
            stage=None,
            error="The server stopped before this finished. Start it again.",
            finished_at=utcnow(),
        )
    )
    db.commit()

    if result.rowcount:
        logger.info(f"Failed {result.rowcount} job(s) left unfinished by a restart")
    return result.rowcount


def _set(db: Session, job_id: int, **values) -> None:
    """Update one job in its own transaction.

    Progress has to be visible to the request that is polling, which is a
    different session, so each change is committed as it is made rather than
    held until the job ends.
    """
    db.execute(update(Job).where(Job.job_id == job_id).values(**values))
    db.commit()
