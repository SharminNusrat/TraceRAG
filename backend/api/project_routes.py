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
    GraphEdgeResponse,
    GraphResponse,
    GraphSummary,
    ProjectConfigResponse,
    ProjectDetailResponse,
    ProjectRequest,
    ProjectResponse,
    ProjectVersionResponse,
    RerunRequest,
    SaveAnalysisRequest,
    TraceLinkResponse,
    VersionSourceRef,
)
from core.auth import get_current_user
from core.db import Analysis, Artifact, Project, User, get_db
from core.db.models import ProjectConfig
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


def to_artifact_response(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=artifact.artifact_id,
        name=artifact.name,
        artifact_type=artifact.artifact_type,
        role=artifact.role,
        file_count=artifact.file_count,
        byte_size=artifact.byte_size,
        uploaded_at=artifact.uploaded_at,
        files_available=all(
            artifact_store.blob_exists(file.sha256) for file in artifact.files
        ),
    )


def to_analysis_summary(
    analysis: Analysis,
    link_count: int,
    artifact_count: int,
    project_name: str,
    files_available: bool = False,
    version_number: int | None = None,
):
    return AnalysisSummaryResponse(
        analysis_id=analysis.analysis_id,
        project_id=analysis.project_id,
        note=analysis.note,
        files_available=files_available,
        classifier_type=analysis.classifier_type,
        top_k=analysis.top_k,
        dependency_expansion_depth=analysis.dependency_expansion_depth,
        execution_duration=analysis.execution_duration,
        created_at=analysis.created_at,
        link_count=link_count,
        artifact_count=artifact_count,
        project_name=project_name,
        version_number=version_number,
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
        summarize_elements=analysis.summarize_elements,
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
        note=analysis.note,
        config=config_of(analysis),
        execution_duration=analysis.execution_duration,
        created_at=analysis.created_at,
        result=result,
        artifacts=[to_artifact_response(a) for a in analysis.artifacts],
    )


def summaries_for(db: Session, rows: list[tuple]) -> list[AnalysisSummaryResponse]:
    """A page of analyses, each saying whether its files are still there."""
    # One query and one filesystem pass for the whole page, rather than per row.
    available = service.analyses_with_files(db, [row[0].analysis_id for row in rows])
    # One lookup for the page, in keeping with the two above it.
    numbers = service.version_numbers(db, [row[0].version_id for row in rows])
    return [
        to_analysis_summary(
            *row,
            files_available=row[0].analysis_id in available,
            version_number=numbers.get(row[0].version_id),
        )
        for row in rows
    ]


def update_graph(
    db: Session, analysis: Analysis, result, renames: dict[str, str] | None = None
) -> None:
    """Fold a saved run into its configuration's graph.

    Which kind sat on each side is read back off the stored artifacts, because
    that is where a run records what it was actually pointed at. An analysis
    whose files were never claimed has nothing to read, and is skipped.

    `renames` is what a sync learned about files that moved. An upload knows
    nothing of the sort and leaves it out.
    """
    kinds = {artifact.role: artifact.artifact_type for artifact in analysis.artifacts}
    service.update_graph(
        db,
        analysis,
        result,
        source_kind=kinds.get(ROLE_SOURCE),
        target_kind=kinds.get(ROLE_TARGET),
        renames=renames,
    )


def resolve_config(db: Session, project: Project, config_id: int | None) -> ProjectConfig:
    """Which configuration a request is asking about.

    Left out when there is only one, because naming it would be ceremony. With
    several it has to be said: guessing would answer a different question from
    the one asked, and the answer would look perfectly reasonable.
    """
    configs = service.list_configs(db, project)
    if not configs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This project has no saved configuration yet.",
        )

    if config_id is None:
        if len(configs) == 1:
            return configs[0]
        default = next((config for config in configs if config.is_default), None)
        if default is not None:
            return default
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This project has {len(configs)} configurations and no default. "
                f"Name the one to read with config_id."
            ),
        )

    chosen = next((config for config in configs if config.config_id == config_id), None)
    if chosen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found in this project.",
        )
    return chosen


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
        analyses=summaries_for(db, rows),
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
        note=request.note,
        config=request.config,
        result=request.result,
        execution_duration=request.execution_duration,
        # These artifacts were just uploaded, so they are a state of the
        # project nothing has been run against before: a new version.
        version_id=service.next_version(db, project).version_id,
    )
    artifact_count = service.claim_artifacts(db, analysis, request.upload_id)
    update_graph(db, analysis, request.result)
    return to_analysis_summary(
        analysis, len(analysis.trace_links), artifact_count, project.project_name,
        # Just written, so anything it claimed is on disk by definition.
        files_available=artifact_count > 0,
    )


@router.get("/projects/{project_id}/configs", response_model=list[ProjectConfigResponse])
def list_configs(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every way this project has been read, with how much each has been used."""
    project = require_project(db, user, project_id)
    counts = service.count_analyses_by_config(db, project)
    return [
        ProjectConfigResponse(
            config_id=config.config_id,
            config_key=config.config_key,
            is_default=config.is_default,
            config=config_of(config),
            analysis_count=counts.get(config.config_id, 0),
            created_at=config.created_at,
        )
        for config in service.list_configs(db, project)
    ]


@router.get("/projects/{project_id}/versions", response_model=list[ProjectVersionResponse])
def list_versions(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every state this project's artifacts have been in, newest first."""
    project = require_project(db, user, project_id)
    return [
        ProjectVersionResponse(
            version_id=version.version_id,
            version_number=version.version_number,
            note=version.note,
            created_at=version.created_at,
            analysis_count=runs,
            sources=[
                VersionSourceRef(
                    source_id=entry.source_id,
                    # Read off the source itself, so a renamed source reads by
                    # the name it has now rather than the one it had then.
                    name=entry.source.name,
                    kind=entry.source.kind,
                    origin=entry.source.origin,
                    ref=entry.ref,
                )
                for entry in version.sources
                if entry.source is not None
            ],
        )
        for version, runs in service.list_versions(db, project)
    ]


@router.get("/projects/{project_id}/graph", response_model=GraphResponse)
def get_graph(
    project_id: int,
    config_id: int | None = Query(default=None, description="Omit when the project has one."),
    link_status: str | None = Query(default=None, description="active, stale or broken."),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A configuration's graph as it currently stands.

    One graph per configuration, so which one has to be named unless the
    project only has the one - two configurations read the same artifacts into
    different elements, and their graphs are not comparable.
    """
    project = require_project(db, user, project_id)
    config = resolve_config(db, project, config_id)

    rows, total = service.list_graph_edges(db, config.config_id, link_status, limit, offset)
    return GraphResponse(
        config_id=config.config_id,
        config_key=config.config_key,
        summary=GraphSummary(**service.graph_summary(db, config.config_id)),
        links=[
            GraphEdgeResponse(
                edge_id=edge.edge_id,
                from_kind=edge.from_kind,
                from_identifier=source.identifier,
                from_present=source.is_active,
                to_kind=edge.to_kind,
                to_identifier=target.identifier,
                to_present=target.is_active,
                confidence=edge.confidence,
                confidence_level=edge.confidence_level,
                explanation=edge.explanation,
                status=edge.status,
            )
            for edge, source, target in rows
        ],
        total=total,
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
    return summaries_for(db, rows)


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
    available = service.analyses_with_files(db, [base.analysis_id, head.analysis_id])
    return ComparisonResponse(
        base=to_analysis_summary(base, len(base.trace_links), len(base.artifacts),
                                 base.project.project_name,
                                 base.analysis_id in available),
        head=to_analysis_summary(head, len(head.trace_links), len(head.artifacts),
                                 head.project.project_name,
                                 head.analysis_id in available),
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
            source_kind=sides[ROLE_SOURCE].artifact_type,
            target_kind=sides[ROLE_TARGET].artifact_type,
            source_preprocessor=config.source_preprocessor,
            target_preprocessor=config.target_preprocessor,
            classifier=config.classifier,
            n_results=config.n_results,
            source_output_level=config.source_output_level,
            target_output_level=config.target_output_level,
            dependency_expansion_depth=config.dependency_expansion_depth,
            summarize_elements=config.summarize_elements,
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
            note=request.note,
            config=config,
            result=result,
            execution_duration=duration,
            # The same files the original ran against, so the same version.
            # Trying a second configuration is not a change to the artifacts.
            version_id=(
                original.version_id
                or service.next_version(db, original.project).version_id
            ),
        )
        copied = service.copy_artifacts(db, original, analysis)
        update_graph(db, analysis, result)

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
