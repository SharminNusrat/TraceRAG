"""Starting an analysis: from uploaded files."""


import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from api.analysis_jobs import run_analysis_job
from api.capabilities import ROLE_SOURCE, ROLE_TARGET, get_capabilities
from api.pipeline_factory import request_default
from api.schemas import (
    AnalysisMode, AnalysisStartResponse,
    CapabilitiesResponse, PreprocessorType, ClassifierType,
)
from api.uploads import materialise_side, parse_id_list, parse_json_field, resolve_side
from api.workspace import get_chroma_path
from core import jobs
from core.auth import get_current_user_optional
from core.db.models import User
from core.db.session import get_db
from core.projects import artifact_store
from core.projects.uploads import UploadBudget, UploadError
from core.schemas import ElementLevel

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities():
    """What the pipeline can ingest. Drives the frontend's option lists."""
    return get_capabilities()


@router.post("/analyze/upload", response_model=AnalysisStartResponse)
async def analyze_upload(
    background: BackgroundTasks,
    artifacts: str = Form(...),
    source_artifact_ids: str = Form(...),
    target_artifact_ids: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    file_paths: str = Form("[]"),
    source_preprocessor: PreprocessorType = Form(request_default("source_preprocessor")),
    target_preprocessor: PreprocessorType = Form(request_default("target_preprocessor")),
    source_output_level: ElementLevel | None = Form(request_default("source_output_level")),
    target_output_level: ElementLevel | None = Form(request_default("target_output_level")),
    classifier: ClassifierType = Form(request_default("classifier")),
    n_results: int = Form(request_default("n_results")),
    dependency_expansion_depth: int = Form(request_default("dependency_expansion_depth")),
    summarize_elements: bool = Form(request_default("summarize_elements")),
    analysis_mode: AnalysisMode = Form(request_default("analysis_mode")),
    project_id: str | None = Form(None),
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Start a run over browser-uploaded artifacts, and return a job to watch.
    Everything after an upload happens in the background, because a
    real run takes minutes and no browser waits that long.
    Anonymous callers may upload.
    """
    artifact_list = parse_json_field(artifacts, "artifacts", [])
    if not isinstance(artifact_list, list) or not artifact_list:
        raise HTTPException(status_code=400, detail="Upload at least one artifact.")

    paths = parse_json_field(file_paths, "file_paths", [])
    if not isinstance(paths, list):
        raise HTTPException(status_code=400, detail="'file_paths' must be a JSON array.")

    source_ids = parse_id_list(source_artifact_ids, "source_artifact_ids")
    target_ids = parse_id_list(target_artifact_ids, "target_artifact_ids")

    if set(source_ids) & set(target_ids):
        raise HTTPException(
            status_code=400,
            detail="An artifact cannot be on both sides of a trace.",
        )

    source_artifacts = resolve_side(artifact_list, source_ids, ROLE_SOURCE)
    target_artifacts = resolve_side(artifact_list, target_ids, ROLE_TARGET)

    use_persistent_cache = analysis_mode == AnalysisMode.PROJECT
    logger.info(f"Using {analysis_mode.value} mode")

    # The workspace outlives the request: the run happens after it, and saving
    # is a separate call that may not come for minutes. Anything nobody saves
    # is reaped on the retention window.
    artifact_store.purge_expired_uploads()
    upload_id, workspace = artifact_store.create_upload_dir()

    # # Isolated index per run prevents data overwrites; cached embeddings keep it fast and free.
    chroma_path = get_chroma_path(f"run-{upload_id}")

    budget = UploadBudget()
    try:
        # Uploaded bytes are read here and nowhere else: an UploadFile is a
        # stream from this request and is gone once the response is sent.
        for role, side in ((ROLE_SOURCE, source_artifacts), (ROLE_TARGET, target_artifacts)):
            materialise_side(side, files, paths, workspace / role, budget)
    except (HTTPException, UploadError):
        artifact_store.discard_upload(upload_id)
        raise

    plan = {
        "source_artifacts": source_artifacts,
        "target_artifacts": target_artifacts,
        "source_preprocessor": source_preprocessor,
        "target_preprocessor": target_preprocessor,
        "source_output_level": source_output_level,
        "target_output_level": target_output_level,
        "classifier": classifier,
        "n_results": n_results,
        "dependency_expansion_depth": dependency_expansion_depth,
        "summarize_elements": summarize_elements,
        "chroma_path": chroma_path,
        "use_persistent_cache": use_persistent_cache,
        "reset_vector_stores": True,
    }

    job = jobs.create_job(
        db,
        user.user_id if user else None,
        int(project_id) if str(project_id or "").isdigit() else None,
        jobs.KIND_ANALYSIS,
    )
    background.add_task(run_analysis_job, job.job_id, upload_id, plan)
    logger.info(f"Analysis job {job.job_id} filed, workspace {upload_id}")
    return AnalysisStartResponse(job_id=job.job_id, token=job.token, upload_id=upload_id)
