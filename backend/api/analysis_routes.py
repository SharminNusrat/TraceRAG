"""Starting an analysis: from paths on the server, or from uploaded files.

The routes only check the request and hand it on. Building the pipeline is in
pipeline_factory, laying the files out in uploads, and the run itself happens
in the background, in analysis_jobs.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from api.analysis_jobs import run_analysis_job
from api.capabilities import ROLE_SOURCE, ROLE_TARGET, get_capabilities
from api.pipeline_factory import build_pipeline_response, provider_for, request_default
from api.schemas import (
    AnalysisMode, AnalysisStartResponse, AnalyzeRequest, AnalyzeResponse,
    CapabilitiesResponse, PreprocessorType, ClassifierType,
)
from api.uploads import materialise_side, parse_id_list, parse_json_field, resolve_side
from api.workspace import get_chroma_path, get_project_id
from core import jobs
from core.auth import get_current_user, get_current_user_optional
from core.db.models import User
from core.db.session import get_db
from core.projects import artifact_store
from core.projects.uploads import UploadBudget, UploadError
from core.schemas import ElementLevel

router = APIRouter()
logger = logging.getLogger(__name__)


def run_analysis(request: AnalyzeRequest) -> AnalyzeResponse:
    chroma_path = "./chroma_data/session"
    use_persistent_cache = False
    reset_vector_stores = True

    if request.analysis_mode == AnalysisMode.PROJECT:
        project_id = get_project_id(request)
        chroma_path = get_chroma_path(project_id)
        use_persistent_cache = True
        reset_vector_stores = False
        logger.info(f"Using project mode with project_id={project_id}")
    else:
        logger.info("Using session mode")

    return build_pipeline_response(
        source_provider=provider_for(request.source, ROLE_SOURCE),
        target_provider=provider_for(request.target, ROLE_TARGET),
        source_kind=request.source.kind,
        target_kind=request.target.kind,
        source_preprocessor=request.source_preprocessor,
        target_preprocessor=request.target_preprocessor,
        classifier=request.classifier,
        n_results=request.n_results,
        source_output_level=request.source_output_level,
        target_output_level=request.target_output_level,
        dependency_expansion_depth=request.dependency_expansion_depth,
        summarize_elements=request.summarize_elements,
        chroma_path=chroma_path,
        use_persistent_cache=use_persistent_cache,
        reset_vector_stores=reset_vector_stores,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities():
    """What the pipeline can ingest. Drives the frontend's option lists."""
    return get_capabilities()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest, user: User = Depends(get_current_user)):
    # Signed-in callers only: this reads a folder on the server by its path,
    # so left open it would hand any visitor whatever the server can read.
    try:
        return run_analysis(request)
    except HTTPException:
        # A rejected request is the caller's problem, not a server fault.
        raise
    except Exception as e:
        import traceback
        logger.error(f"Pipeline failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


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

    The uploaded bytes are read here - an upload is a stream belonging to this
    request - and everything after that happens in the background, because a
    real run takes minutes and no browser waits that long.


    `artifacts` is a JSON array describing each side's material:
        [{"id","name","kind","file_indexes":[...]},   uploaded files
         {"id","name","kind","text"}]                 pasted text

    `file_indexes` point into `files`, and `file_paths` carries each file's
    relative path so folder uploads keep their structure.

    Each side takes a list of artifact ids. Several artifacts on one side are
    analysed together as a single corpus - that is how a set of loose code
    files becomes one codebase. Only the referenced artifacts are written to
    disk.

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

    # Project mode keeps the model caches - embeddings, summaries, verdicts -
    # between runs, so re-running the same files pays for nothing twice.
    use_persistent_cache = analysis_mode == AnalysisMode.PROJECT
    logger.info(f"Using {analysis_mode.value} mode")

    # The workspace outlives the request: the run happens after it, and saving
    # is a separate call that may not come for minutes. Anything nobody saves
    # is reaped on the retention window.
    artifact_store.purge_expired_uploads()
    upload_id, workspace = artifact_store.create_upload_dir()

    # Every run indexes into a directory of its own: two runs sharing one
    # would replace each other's elements, and a New Analysis is not yet any
    # analysis whose index it could reuse. The embeddings themselves are
    # cached by content, so a fresh index costs no model calls.
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
