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
from api.workspace import get_chroma_path, get_project_id, upload_project_id
from core import jobs
from core.auth import get_current_user_optional
from core.db.models import Project, ProjectSource, User
from core.db.session import get_db
from core.projects import artifact_store, service
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
async def analyze(request: AnalyzeRequest):
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


def require_owned_project(db: Session, user: User | None, project_id: str | None) -> Project:
    """The project a connected source is being read from.

    Uploading needs no account, but fetching from a source does: the source
    belongs to a project, and reading it is reading whatever that project's
    stored credentials can reach.
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to run an analysis from a connected source.",
        )

    numeric = str(project_id or "").strip()
    if not numeric.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Running from a connected source needs the project it belongs to.",
        )

    project = service.get_project(db, user.user_id, int(numeric))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def side_source(
    db: Session, project: Project, artifacts: list[dict], role: str
) -> ProjectSource | None:
    """The connected source a side is taken from, if it is not uploaded.

    A side is one or the other. Half a codebase fetched and half uploaded is
    not a thing anyone means, and allowing it would leave the source recording
    a commit it does not actually hold.
    """
    named = [artifact for artifact in artifacts if artifact.get("source_id")]
    if not named:
        return None

    if len(named) != len(artifacts) or len({a["source_id"] for a in named}) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The {role} side must be either uploaded or taken from a single "
                f"connected source, not a mixture."
            ),
        )

    source = service.get_source(db, project, int(named[0]["source_id"]))
    if source is None:
        raise HTTPException(
            status_code=400,
            detail=f"No connected source matches the one chosen for the {role} side.",
        )
    if source.kind != artifacts[0]["kind"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{source.name}' supplies {source.kind}, which is not what the "
                f"{role} side is set to."
            ),
        )
    return source


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
         {"id","name","kind","text"},                 pasted text
         {"id","name","kind","source_id": 4}]         a connected source

    `file_indexes` point into `files`, and `file_paths` carries each file's
    relative path so folder uploads keep their structure. An artifact naming a
    `source_id` is fetched from that source instead - which is how a codebase
    already on GitHub never has to be uploaded by hand.

    Each side takes a list of artifact ids. Several artifacts on one side are
    analysed together as a single corpus - that is how a set of loose code
    files becomes one codebase. Only the referenced artifacts are written to
    disk.

    Anonymous callers may upload; fetching from a connected source needs the
    signed-in owner of the project it belongs to.
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

    # A side may be taken from something the project is already connected to
    # rather than uploaded. That belongs to a project, so it needs the project
    # and its owner - neither of which an anonymous run has.
    project = None
    if any(a.get("source_id") for a in source_artifacts + target_artifacts):
        project = require_owned_project(db, user, project_id)
    side_sources = {
        ROLE_SOURCE: side_source(db, project, source_artifacts, ROLE_SOURCE) if project else None,
        ROLE_TARGET: side_source(db, project, target_artifacts, ROLE_TARGET) if project else None,
    }

    chroma_path = "./chroma_data/session"
    use_persistent_cache = False
    reset_vector_stores = True

    if analysis_mode == AnalysisMode.PROJECT:
        # Project mode persists the embedding cache between runs, so re-running
        # the same codebase skips re-embedding unchanged content.
        resolved_project_id = upload_project_id(
            source_artifacts + target_artifacts, paths, project_id
        )
        chroma_path = get_chroma_path(resolved_project_id)
        use_persistent_cache = True
        reset_vector_stores = False
        logger.info(f"Using project mode with project_id={resolved_project_id}")
    else:
        logger.info("Using session mode")

    # The workspace outlives the request: the run happens after it, and saving
    # is a separate call that may not come for minutes. Anything nobody saves
    # is reaped on the retention window.
    artifact_store.purge_expired_uploads()
    upload_id, workspace = artifact_store.create_upload_dir()

    # Session runs used to share one directory, so two of them at once wiped
    # each other's vectors. Each gets its own now that runs outlive requests.
    if analysis_mode != AnalysisMode.PROJECT:
        chroma_path = get_chroma_path(f"session-{upload_id}")

    budget = UploadBudget()
    try:
        # Uploaded bytes are read here and nowhere else: an UploadFile is a
        # stream from this request and is gone once the response is sent.
        for role, side in ((ROLE_SOURCE, source_artifacts), (ROLE_TARGET, target_artifacts)):
            if side_sources[role] is None:
                materialise_side(side, files, paths, workspace / role, budget)
    except (HTTPException, UploadError):
        artifact_store.discard_upload(upload_id)
        raise

    plan = {
        "source_artifacts": source_artifacts,
        "target_artifacts": target_artifacts,
        # Ids, not rows: the background half opens its own session.
        "source_ids": {role: (s.source_id if s else None) for role, s in side_sources.items()},
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
        "reset_vector_stores": reset_vector_stores,
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
