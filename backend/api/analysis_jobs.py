"""The background half of an analysis: what runs after /analyze/upload has answered."""

import logging
import time

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.capabilities import ROLE_SOURCE, ROLE_TARGET
from api.pipeline_factory import build_pipeline_response, build_provider
from api.schemas import AnalyzeResponse
from api.uploads import side_manifest
from api.workspace import relativize_response
from core import jobs
from core.db.models import ProjectSource
from core.db.session import SessionLocal
from core.projects import artifact_store
from core.sync import SyncError, fetch_source

logger = logging.getLogger(__name__)


def progress_writer(db: Session, job_id: int):
    """A progress callback that does not write to the database on every element.

    One row update per element would be a hundred commits for a run nobody is
    reading that fast. A second apart is more than enough to watch, and the
    final step of each stage always lands so the bar does not stop short.
    """
    last_written = 0.0

    def report(stage: str, current: int = 0, total: int = 0) -> None:
        nonlocal last_written
        now = time.monotonic()
        if current and current != total and now - last_written < 1.0:
            return
        last_written = now
        jobs.set_stage(db, job_id, stage, current, total)

    return report


def perform_analysis(db: Session, upload_id: str, plan: dict, job_id: int) -> AnalyzeResponse:
    """Fetch whatever was not uploaded, then run the pipeline over the lot."""
    workspace = artifact_store.upload_dir(upload_id)
    if workspace is None or not workspace.is_dir():
        raise SyncError("The uploaded files are no longer available.")

    refs = {}
    for role in (ROLE_SOURCE, ROLE_TARGET):
        source_id = plan["source_ids"][role]
        if source_id is None:
            continue
        source = db.get(ProjectSource, source_id)
        if source is None:
            raise SyncError("A connected source was removed before the run started.")
        jobs.set_stage(db, job_id, f"Fetching {source.name}")
        # The commit comes back so the saved run records what it analysed.
        refs[role] = fetch_source(source, workspace / role)

    source_artifacts = plan["source_artifacts"]
    target_artifacts = plan["target_artifacts"]
    artifact_store.write_manifest(workspace, [
        side_manifest(source_artifacts, ROLE_SOURCE,
                      db.get(ProjectSource, plan["source_ids"][ROLE_SOURCE])
                      if plan["source_ids"][ROLE_SOURCE] else None, refs.get(ROLE_SOURCE)),
        side_manifest(target_artifacts, ROLE_TARGET,
                      db.get(ProjectSource, plan["source_ids"][ROLE_TARGET])
                      if plan["source_ids"][ROLE_TARGET] else None, refs.get(ROLE_TARGET)),
    ])

    source_dir, target_dir = workspace / ROLE_SOURCE, workspace / ROLE_TARGET
    response = build_pipeline_response(
        source_provider=build_provider(source_artifacts[0]["kind"], source_dir),
        target_provider=build_provider(target_artifacts[0]["kind"], target_dir),
        source_kind=source_artifacts[0]["kind"],
        target_kind=target_artifacts[0]["kind"],
        source_preprocessor=plan["source_preprocessor"],
        target_preprocessor=plan["target_preprocessor"],
        classifier=plan["classifier"],
        n_results=plan["n_results"],
        source_output_level=plan["source_output_level"],
        target_output_level=plan["target_output_level"],
        dependency_expansion_depth=plan["dependency_expansion_depth"],
        summarize_elements=plan["summarize_elements"],
        chroma_path=plan["chroma_path"],
        use_persistent_cache=plan["use_persistent_cache"],
        reset_vector_stores=plan["reset_vector_stores"],
        on_progress=progress_writer(db, job_id),
    )
    response.upload_id = upload_id
    return relativize_response(response, [source_dir, target_dir])


def run_analysis_job(job_id: int, upload_id: str, plan: dict) -> None:
    """The background half of a run. Owns its own session.

    The request's session is closed by the time this runs - the response has
    already gone out - so nothing from it can be carried in here.
    """
    with SessionLocal() as db:
        jobs.start(db, job_id)
        try:
            response = perform_analysis(db, upload_id, plan, job_id)
            jobs.succeed(db, job_id, response.model_dump(mode="json"))
            logger.info(
                f"Analysis job {job_id} finished: {len(response.trace_links)} trace links"
            )
        except Exception as error:
            # The files go with it: nothing produced a result, so there is
            # nothing to save and nothing worth keeping on disk.
            artifact_store.discard_upload(upload_id)
            logger.error(f"Analysis job {job_id} failed: {error}", exc_info=True)
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            jobs.fail(db, job_id, str(detail))
