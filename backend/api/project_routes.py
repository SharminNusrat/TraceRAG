"""Projects and saved analyses.

Every endpoint needs a signed-in user. Anything the caller does not own returns
404 rather than 403, so responses cannot be used to map out which ids exist.
"""

import logging
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.capabilities import ROLE_SOURCE, ROLE_TARGET
from api.routes import (
    build_pipeline_response,
    build_provider,
    get_chroma_path,
    relativize_response,
)
from api.schemas import (
    AnalysisConfig,
    AnalysisDetailResponse,
    AnalysisSummaryResponse,
    AnalyzeResponse,
    ArtifactResponse,
    ComparisonResponse,
    ElementResponse,
    ProjectDetailResponse,
    ProjectRequest,
    ProjectResponse,
    RerunRequest,
    SaveAnalysisRequest,
    TraceLinkResponse,
)
from core.auth import get_current_user
from core.db import Analysis, Artifact, Project, User, get_db
from core.projects import artifact_store, service

router = APIRouter(tags=["projects"])
logger = logging.getLogger(__name__)

PROJECT_NOT_FOUND = "Project not found."
ANALYSIS_NOT_FOUND = "Analysis not found."
ARTIFACT_NOT_FOUND = "Artifact not found."


# ----- Response builders -----

def to_project_response(project: Project, analysis_count: int) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        project_name=project.project_name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        analysis_count=analysis_count,
    )


def to_analysis_summary(
    analysis: Analysis, link_count: int, artifact_count: int, project_name: str
):
    return AnalysisSummaryResponse(
        analysis_id=analysis.analysis_id,
        project_id=analysis.project_id,
        version_name=analysis.version_name,
        classifier_type=analysis.classifier_type,
        top_k=analysis.top_k,
        dependency_expansion_depth=analysis.dependency_expansion_depth,
        execution_duration=analysis.execution_duration,
        created_at=analysis.created_at,
        link_count=link_count,
        artifact_count=artifact_count,
        project_name=project_name,
    )


def config_of(analysis: Analysis) -> AnalysisConfig:
    """The settings a stored analysis was run with."""
    return AnalysisConfig(
        source_preprocessor=analysis.source_preprocessor,
        target_preprocessor=analysis.target_preprocessor,
        source_output_level=analysis.source_output_level,
        target_output_level=analysis.target_output_level,
        classifier=analysis.classifier_type,
        n_results=analysis.top_k,
        dependency_expansion_depth=analysis.dependency_expansion_depth,
    )


def to_analysis_detail(analysis: Analysis) -> AnalysisDetailResponse:
    """Reassemble a stored run into the shape the results view already reads."""
    snapshot = service.load_snapshot(analysis)

    result = AnalyzeResponse(
        trace_links=[
            TraceLinkResponse(
                source_id=link.source_id,
                source_content=link.source_content,
                target_id=link.target_id,
                target_content=link.target_content,
                confidence=link.similarity_score,
                confidence_level=link.confidence_level,
                explanation=link.explanation,
            )
            for link in analysis.trace_links
        ],
        source_elements=[ElementResponse(**e) for e in snapshot.get("source_elements", [])],
        target_elements=[ElementResponse(**e) for e in snapshot.get("target_elements", [])],
        unimplemented=snapshot.get("unimplemented", []),
        summary=snapshot.get("summary", {}),
    )

    return AnalysisDetailResponse(
        analysis_id=analysis.analysis_id,
        project_id=analysis.project_id,
        project_name=analysis.project.project_name,
        version_name=analysis.version_name,
        config=config_of(analysis),
        execution_duration=analysis.execution_duration,
        created_at=analysis.created_at,
        result=result,
        artifacts=[ArtifactResponse.model_validate(a) for a in analysis.artifacts],
    )


def require_project(db: Session, user: User, project_id: int) -> Project:
    project = service.get_project(db, user.user_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND)
    return project


def require_analysis(db: Session, user: User, analysis_id: int) -> Analysis:
    analysis = service.get_analysis(db, user.user_id, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ANALYSIS_NOT_FOUND)
    return analysis


# ----- Projects -----

@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    request: ProjectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = service.create_project(db, user.user_id, request.project_name, request.description)
    return to_project_response(project, 0)


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [
        to_project_response(project, count)
        for project, count in service.list_projects(db, user.user_id)
    ]


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project(db, user, project_id)
    rows = service.list_analyses(db, user.user_id, project_id=project_id)

    return ProjectDetailResponse(
        **to_project_response(project, len(rows)).model_dump(),
        analyses=[to_analysis_summary(*row) for row in rows],
    )


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deletes the project along with every analysis and link inside it."""
    project = require_project(db, user, project_id)
    service.delete_project(db, project)


# ----- Saved analyses -----

@router.post(
    "/projects/{project_id}/analyses",
    response_model=AnalysisSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_analysis(
    project_id: int,
    request: SaveAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project(db, user, project_id)
    analysis = service.save_analysis(
        db,
        project,
        version_name=request.version_name,
        config=request.config,
        result=request.result,
        execution_duration=request.execution_duration,
    )
    artifact_count = service.claim_artifacts(db, analysis, request.upload_id)
    return to_analysis_summary(
        analysis, len(analysis.trace_links), artifact_count, project.project_name
    )


@router.get("/analyses", response_model=list[AnalysisSummaryResponse])
def list_analyses(
    project_id: int | None = None,
    limit: int = Query(default=service.DEFAULT_HISTORY_LIMIT, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Recent saved runs, newest first, across every project by default."""
    rows = service.list_analyses(db, user.user_id, project_id=project_id, limit=limit)
    return [to_analysis_summary(*row) for row in rows]


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetailResponse)
def get_analysis(
    analysis_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return to_analysis_detail(require_analysis(db, user, analysis_id))


@router.delete("/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis(
    analysis_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deletes the analysis, its links, and the artifacts it was run against."""
    analysis = require_analysis(db, user, analysis_id)
    service.delete_analysis(db, analysis)


@router.get("/analyses/{base_id}/compare/{head_id}", response_model=ComparisonResponse)
def compare_analyses(
    base_id: int,
    head_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Diff two saved runs of the same project."""
    base = require_analysis(db, user, base_id)
    head = require_analysis(db, user, head_id)

    if base.analysis_id == head.analysis_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pick two different analyses to compare.",
        )
    if base.project_id != head.project_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analyses can only be compared within the same project.",
        )

    diff = service.compare_analyses(db, base, head)
    return ComparisonResponse(
        base=to_analysis_summary(base, len(base.trace_links), len(base.artifacts),
                                 base.project.project_name),
        head=to_analysis_summary(head, len(head.trace_links), len(head.artifacts),
                                 head.project.project_name),
        **diff,
    )


@router.post(
    "/analyses/{analysis_id}/rerun",
    response_model=AnalysisDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def rerun_analysis(
    analysis_id: int,
    request: RerunRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run a saved analysis again and store the result as a new one."""
    # Artifacts are restored from the blob store rather than re-uploaded, and
    # the new analysis shares the original's blobs - rows, not bytes.
    original = require_analysis(db, user, analysis_id)

    sides = {artifact.role: artifact for artifact in original.artifacts}
    if ROLE_SOURCE not in sides or ROLE_TARGET not in sides:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This analysis has no stored artifacts to re-run. Only analyses "
                "saved from an upload keep their files."
            ),
        )

    config = request.config or config_of(original)
    workspace = Path(tempfile.mkdtemp(prefix="tracerag-rerun-"))

    try:
        directories = {}
        for role, artifact in sides.items():
            entries = [(f.relative_path, f.sha256) for f in artifact.files]
            restored = artifact_store.materialise(entries, workspace / role)
            if not restored:
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=f"The stored {role} files are no longer on disk.",
                )
            directories[role] = workspace / role

        started = time.perf_counter()
        result = build_pipeline_response(
            source_provider=build_provider(sides[ROLE_SOURCE].artifact_type,
                                           directories[ROLE_SOURCE]),
            target_provider=build_provider(sides[ROLE_TARGET].artifact_type,
                                           directories[ROLE_TARGET]),
            source_preprocessor=config.source_preprocessor,
            target_preprocessor=config.target_preprocessor,
            classifier=config.classifier,
            n_results=config.n_results,
            source_output_level=config.source_output_level,
            target_output_level=config.target_output_level,
            dependency_expansion_depth=config.dependency_expansion_depth,
            chroma_path=get_chroma_path(f"project-{original.project_id}"),
            # The embedding cache is keyed by content, so unchanged text costs
            # nothing to re-embed. The vector store is rebuilt, because a
            # different preprocessor produces different elements and stale ones
            # would otherwise stay retrievable.
            use_persistent_cache=True,
            reset_vector_stores=True,
        )
        duration = time.perf_counter() - started

        # Identifiers would otherwise carry the temp workspace prefix.
        result = relativize_response(result, list(directories.values()))

        analysis = service.save_analysis(
            db,
            original.project,
            version_name=request.version_name,
            config=config,
            result=result,
            execution_duration=duration,
        )
        copied = service.copy_artifacts(db, original, analysis)

        logger.info(
            f"Re-ran analysis {analysis_id} as {analysis.analysis_id} "
            f"({len(result.trace_links)} links, {copied} artifacts reused) in {duration:.1f}s"
        )
        return to_analysis_detail(analysis)

    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Re-run of analysis {analysis_id} failed: {error}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(error))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(
    artifact_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream one stored artifact back as a zip, folder structure intact."""
    artifact = db.scalar(
        select(Artifact)
        .join(Analysis, Analysis.analysis_id == Artifact.analysis_id)
        .join(Project, Project.project_id == Analysis.project_id)
        .where(Artifact.artifact_id == artifact_id, Project.user_id == user.user_id)
    )
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ARTIFACT_NOT_FOUND)

    # Names come from the rows, bytes from the blob store - the store is keyed
    # by content and knows nothing about what anything was called.
    entries = [(f.relative_path, f.sha256) for f in artifact.files]
    if not entries or not any(artifact_store.blob_path(d).exists() for _, d in entries):
        # The rows outlived their blobs - possible if the data directory was
        # moved or cleared by hand. Say so rather than serving an empty archive.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The stored files for this artifact are no longer on disk.",
        )

    buffer = artifact_store.build_zip(entries)
    filename = f"{artifact.role}-{artifact.artifact_type}-{artifact.artifact_id}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
