"""Projects, analyses and their saved runs."""

import json
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
from api.changes import NOT_TEXT, FilesMissing, line_diff, net_changes, preprocessor_of
from api.pipeline_factory import build_pipeline_response, build_provider, name_elements
from api.workspace import get_chroma_path, relativize_response
from api.schemas import (
    AnalysisConfig,
    AnalysisDetailResponse,
    AnalysisSummaryResponse,
    AnalyzeResponse,
    ArtifactResponse,
    ChangeReportResponse,
    ElementResponse,
    LineDiffResponse,
    NetSide,
    ProjectConfigResponse,
    ProjectDetailResponse,
    ProjectRequest,
    ProjectResponse,
    ProjectVersionResponse,
    ReportVersion,
    RerunRequest,
    VersionRun,
    SaveAnalysisRequest,
    SideChangesResponse,
    SourceResponse,
    TraceLinkResponse,
    VersionSourceRef,
)
from core.auth import get_current_user
from core.db import Analysis, Artifact, Project, User, get_db
from core.db.models import ProjectConfig, ProjectSource, ProjectVersion
from core.projects import artifact_store, service
from core.projects.diff import net_summary
from core.projects.report import count_file_links

router = APIRouter(tags=["projects"])
logger = logging.getLogger(__name__)

PROJECT_NOT_FOUND = "Project not found."
CONFIG_NOT_FOUND = "Analysis not found."
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


def to_source_response(
    source: ProjectSource, held: Artifact | None = None, with_files: bool = False
) -> SourceResponse:
    """A side as a client may see it, which is everything but the token.

    `held` is the side's file set in the newest version, which is what the
    side currently holds.
    """
    return SourceResponse(
        source_id=source.source_id,
        config_id=source.config_id,
        role=source.role,
        kind=source.kind,
        name=source.name,
        origin=source.origin,
        location=source.location,
        branch=source.branch,
        last_sync_ref=held.ref if held else None,
        has_token=bool(source.access_token),
        files=sorted(file.relative_path for file in held.files) if held and with_files else [],
    )


def stored_changes(version: ProjectVersion) -> list[SideChangesResponse]:
    """What changed since the version before, as the version recorded it."""
    if not version.changes_json:
        return []
    return [
        SideChangesResponse(
            role=side["role"],
            files=side["files"],
            elements=side["elements"],
            changed=any(side["files"].values()),
            meaningful=any(side["elements"].values()),
        )
        for side in json.loads(version.changes_json).values()
    ]


def held_by_role(version: ProjectVersion | None) -> dict[str, Artifact]:
    """A version's file sets, by the side they belong to."""
    return {artifact.role: artifact for artifact in version.artifacts} if version else {}


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
        config_id=analysis.config_id,
        note=analysis.note,
        files_available=files_available,
        classifier_type=analysis.config.classifier_type,
        top_k=analysis.config.top_k,
        dependency_expansion_depth=analysis.config.dependency_expansion_depth,
        execution_duration=analysis.execution_duration,
        created_at=analysis.created_at,
        link_count=link_count,
        artifact_count=artifact_count,
        project_name=project_name,
        version_number=version_number,
    )


def config_of(config: ProjectConfig) -> AnalysisConfig:
    """The settings an analysis runs with."""
    return AnalysisConfig(
        source_preprocessor=config.source_preprocessor,
        target_preprocessor=config.target_preprocessor,
        source_output_level=config.source_output_level,
        target_output_level=config.target_output_level,
        classifier=config.classifier_type,
        n_results=config.top_k,
        dependency_expansion_depth=config.dependency_expansion_depth,
        summarize_elements=config.summarize_elements,
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

    name_elements(result)
    return AnalysisDetailResponse(
        analysis_id=analysis.analysis_id,
        project_id=analysis.project_id,
        config_id=analysis.config_id,
        version_number=analysis.version.version_number,
        project_name=analysis.project.project_name,
        note=analysis.note,
        config=config_of(analysis.config),
        execution_duration=analysis.execution_duration,
        created_at=analysis.created_at,
        result=result,
        artifacts=[to_artifact_response(a) for a in analysis.artifacts],
    )


def summaries_for(db: Session, rows: list[tuple]) -> list[AnalysisSummaryResponse]:
    """A page of runs, each saying whether its files are still there."""
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


def to_config_response(
    db: Session, config: ProjectConfig, with_files: bool = False
) -> ProjectConfigResponse:
    """An analysis, with where it stands now: its newest version and run."""
    latest = service.latest_analysis(db, config)
    version = service.latest_version(db, config)
    held = held_by_role(version)
    return ProjectConfigResponse(
        config_id=config.config_id,
        project_id=config.project_id,
        project_name=config.project.project_name,
        source_kind=config.source_kind,
        target_kind=config.target_kind,
        config=config_of(config),
        analysis_count=service.count_runs(db, config),
        version_number=version.version_number if version else None,
        latest_analysis_id=latest.analysis_id if latest else None,
        latest_run_at=latest.created_at if latest else None,
        link_count=len(latest.trace_links) if latest else 0,
        sides=[
            to_source_response(side, held.get(side.role), with_files)
            for side in service.list_sides(db, config)
        ],
        created_at=config.created_at,
    )


def upload_kinds(upload_id: str | None) -> dict[str, str]:
    """The artifact kind on each side of a pending upload, by role.

    Read off the upload's manifest, which is the only place a fresh run records
    what it was pointed at. Empty when there is no upload to read - the run is
    then saved without its files, and without a kind to file it under.
    """
    directory = artifact_store.upload_dir(upload_id) if upload_id else None
    if directory is None or not directory.is_dir():
        return {}
    return {
        entry.get("role"): entry.get("artifact_type")
        for entry in artifact_store.read_manifest(directory)
    }


def require_project(db: Session, user: User, project_id: int) -> Project:
    project = service.get_project(db, user.user_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND)
    return project


def require_config(db: Session, user: User, project_id: int, config_id: int) -> ProjectConfig:
    """One analysis of one of this user's projects."""
    config = service.get_config(db, user.user_id, config_id)
    if config is None or config.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CONFIG_NOT_FOUND)
    return config


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
        **to_project_response(project, len(service.list_configs(db, project))).model_dump(),
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


# ----- Analyses -----

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
    """Save a New Analysis: a new analysis at version 1, holding this run."""
    project = require_project(db, user, project_id)
    kinds = upload_kinds(request.upload_id)
    # Always a new analysis, whatever its settings: an earlier one with the
    # same settings is a different relation with a history of its own.
    config = service.create_config(
        db, project, request.config, kinds.get(ROLE_SOURCE), kinds.get(ROLE_TARGET)
    )
    version = service.next_version(db, config)
    analysis = service.save_analysis(
        db, config, version.version_id,
        note=request.note,
        result=request.result,
        execution_duration=request.execution_duration,
    )
    artifact_count = service.claim_artifacts(db, version, request.upload_id)
    service.keep_pins(db, analysis, request.result)
    return to_analysis_summary(
        analysis, len(analysis.trace_links), artifact_count, project.project_name,
        # Just written, so anything it claimed is on disk by definition.
        files_available=artifact_count > 0,
        version_number=version.version_number,
    )


@router.get("/configs", response_model=list[ProjectConfigResponse])
def list_all_configs(
    project_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every analysis this user has, newest first, across every project by default."""
    return [
        to_config_response(db, config)
        for config in service.list_user_configs(db, user.user_id, project_id)
    ]


@router.get("/projects/{project_id}/configs", response_model=list[ProjectConfigResponse])
def list_configs(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every analysis in this project, with where each one stands."""
    project = require_project(db, user, project_id)
    return [to_config_response(db, config) for config in service.list_configs(db, project)]


@router.get(
    "/projects/{project_id}/configs/{config_id}", response_model=ProjectConfigResponse
)
def get_config(
    project_id: int,
    config_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One analysis, with both sides and the files each holds now."""
    return to_config_response(db, require_config(db, user, project_id, config_id), with_files=True)


@router.delete(
    "/projects/{project_id}/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_config(
    project_id: int,
    config_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deletes an analysis with its sides, versions, runs and pins."""
    service.delete_config(db, require_config(db, user, project_id, config_id))


@router.get(
    "/projects/{project_id}/configs/{config_id}/versions",
    response_model=list[ProjectVersionResponse],
)
def list_versions(
    project_id: int,
    config_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every state this analysis's files have been in, newest first."""
    config = require_config(db, user, project_id, config_id)
    sides = {side.role: side for side in service.list_sides(db, config)}
    return [
        ProjectVersionResponse(
            version_id=version.version_id,
            version_number=version.version_number,
            note=version.note,
            created_at=version.created_at,
            analysis_count=runs,
            sources=[
                VersionSourceRef(
                    source_id=sides[artifact.role].source_id,
                    role=artifact.role,
                    name=artifact.name,
                    kind=artifact.artifact_type,
                    origin=artifact.origin,
                    ref=artifact.ref or "",
                )
                for artifact in version.artifacts
                if artifact.role in sides
            ],
            changes=stored_changes(version),
            runs=[
                VersionRun(
                    analysis_id=run.analysis_id,
                    note=run.note,
                    created_at=run.created_at,
                    link_count=len(run.trace_links),
                )
                for run in sorted(version.analyses, key=lambda run: run.created_at, reverse=True)
            ],
        )
        for version, runs in service.list_versions(db, config)
    ]


@router.get(
    "/projects/{project_id}/configs/{config_id}/report",
    response_model=ChangeReportResponse,
)
def change_report(
    project_id: int,
    config_id: int,
    base: int | None = Query(default=None, description="The earlier version. Defaults to the one before `head`."),
    head: int | None = Query(default=None, description="The later version. Defaults to the newest."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What changed between two versions of an analysis: files, elements and every link."""
    config = require_config(db, user, project_id, config_id)
    try:
        report = service.version_report(db, config, base, head)
    except service.ReportError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))

    net, net_available = [], True
    if report["base_version"] is not None:
        by_number = {version.version_number: version for version in config.versions}
        try:
            sides = net_changes(
                config, by_number[report["base_version"]], by_number[report["head_version"]],
                report["versions"],
            )
        except FilesMissing:
            net_available = False
            sides = []
        for changes in sides:
            summary = net_summary(changes)
            count_file_links(summary["files"], report["links"], changes.role)
            net.append(NetSide(
                **summary, whole_documents=preprocessor_of(config, changes.role) == "single",
            ))

    return ChangeReportResponse(
        config_id=config.config_id,
        **{key: value for key, value in report.items() if key != "versions"},
        net=net,
        net_available=net_available,
        versions=[
            ReportVersion(
                version_number=version.version_number,
                note=version.note,
                created_at=version.created_at,
                changes=stored_changes(version),
            )
            for version in report["versions"]
        ],
    )


@router.get(
    "/projects/{project_id}/configs/{config_id}/report/diff",
    response_model=LineDiffResponse,
)
def file_line_diff(
    project_id: int,
    config_id: int,
    base: int = Query(description="The earlier version."),
    head: int = Query(description="The later version."),
    role: str = Query(description="source or target."),
    path: str = Query(description="The file's path in the later version."),
    old_path: str | None = Query(default=None, description="Its path in the earlier one, if renamed."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One changed file's text, earlier version against later, read from the stored files."""
    config = require_config(db, user, project_id, config_id)
    if path.lower().endswith(NOT_TEXT):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No line diff for this file type.")

    by_number = {version.version_number: version for version in config.versions}

    def stored(number: int, wanted: str) -> bytes:
        """A file's bytes at one version; empty when it is not there."""
        version = by_number.get(number)
        artifact = next((a for a in version.artifacts if a.role == role), None) if version else None
        file = next((f for f in artifact.files if f.relative_path == wanted), None) if artifact else None
        if file is None:
            return b""
        content = artifact_store.open_blob(file.sha256)
        if content is None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="The stored file is no longer on disk.",
            )
        return content

    if base not in by_number or head not in by_number:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found.")
    lines, truncated = line_diff(stored(base, old_path or path), stored(head, path))
    return LineDiffResponse(lines=lines, truncated=truncated)


# ----- Runs -----

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
    """Deletes one run and its links. Its analysis and versions stay."""
    analysis = require_analysis(db, user, analysis_id)
    if service.run_count(db, analysis.version_id) == 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the only run of this version.",
        )
    service.delete_analysis(db, analysis)


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
    """Run an analysis again over the same files, with the same settings."""
    original = require_analysis(db, user, analysis_id)
    config = original.config

    sides = {artifact.role: artifact for artifact in original.artifacts}
    if ROLE_SOURCE not in sides or ROLE_TARGET not in sides:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This analysis has no stored artifacts to re-run. Only analyses "
                "saved from an upload keep their files."
            ),
        )

    settings = config_of(config)
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
            source_preprocessor=settings.source_preprocessor,
            target_preprocessor=settings.target_preprocessor,
            classifier=settings.classifier,
            n_results=settings.n_results,
            source_output_level=settings.source_output_level,
            target_output_level=settings.target_output_level,
            dependency_expansion_depth=settings.dependency_expansion_depth,
            summarize_elements=settings.summarize_elements,
            chroma_path=get_chroma_path(f"analysis-{config.config_id}"),
            # The embedding cache is keyed by content, so unchanged text costs
            # nothing to re-embed. The vector store is rebuilt so the index
            # holds exactly these files' elements and nothing older.
            use_persistent_cache=True,
            reset_vector_stores=True,
        )
        duration = time.perf_counter() - started

        # Identifiers would otherwise carry the temp workspace prefix.
        result = relativize_response(result, list(directories.values()))

        analysis = service.save_analysis(
            db, config, original.version_id,
            note=request.note,
            result=result,
            execution_duration=duration,
        )
        service.keep_pins(db, analysis, result)

        logger.info(
            f"Re-ran analysis {analysis_id} as {analysis.analysis_id} "
            f"({len(result.trace_links)} links) in {duration:.1f}s"
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
        .join(ProjectVersion, ProjectVersion.version_id == Artifact.version_id)
        .join(ProjectConfig, ProjectConfig.config_id == ProjectVersion.config_id)
        .join(Project, Project.project_id == ProjectConfig.project_id)
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
