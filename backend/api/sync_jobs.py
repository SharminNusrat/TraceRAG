"""Updating one side of an analysis: assembling its files, re-running it, keeping the result."""

import json
import logging
import shutil
import time
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from api.capabilities import ROLE_SOURCE, ROLE_TARGET
from api.changes import compare_file_sets, copy_from
from api.project_routes import config_of
from api.pipeline_factory import build_pipeline_response, build_provider
from api.workspace import get_chroma_path, relativize_response
from api.schemas import (
    AnalyzeResponse, SideChangesResponse, SyncRequest, SyncResponse, SyncSourceResult,
)
from core.db.models import Artifact, ProjectConfig, ProjectSource, ProjectVersion
from core.db.session import SessionLocal
from core import jobs
from core.projects import ORIGIN_GITHUB, ORIGIN_UPLOAD, artifact_store, service
from core.projects.diff import IdMap, SideChanges, translate_pins
from core.projects.report import change_report
from core.sync import SyncError, fetch_source, renames_since

logger = logging.getLogger(__name__)


def require_files(db: Session, config: ProjectConfig) -> ProjectVersion:
    """The analysis's newest version, which is what an update starts from."""
    latest = service.latest_version(db, config)
    if latest is None or not latest.artifacts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This analysis has no stored files, so there is nothing to update. "
                "Run and save it from an upload first."
            ),
        )
    return latest


def staged_dir(upload_id: str) -> Path:
    """The staged upload behind an id, or a refusal the user can act on."""
    directory = artifact_store.upload_dir(upload_id)
    if directory is None or not directory.is_dir():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "Those uploaded files are no longer waiting - staged uploads are "
                "cleared after a while. Upload them again."
            ),
        )
    return directory


def side_changes(
    config: ProjectConfig,
    held: Artifact,
    directory: Path,
    renames: dict[str, str] | None = None,
) -> SideChanges:
    """What giving a side the files in `directory` would change."""
    return compare_file_sets(
        config, held.role, held.artifact_type,
        {file.relative_path: file.sha256 for file in held.files},
        {path: artifact_store.hash_file(file) for path, file in artifact_store.collect_files(directory)},
        copy_from(directory), renames,
    )


def changes_response(changes: SideChanges) -> SideChangesResponse:
    stored = changes.to_dict()
    return SideChangesResponse(
        role=changes.role,
        files=stored["files"],
        elements=stored["elements"],
        changed=changes.files.changed,
        meaningful=changes.elements.meaningful,
    )


def no_change_detail(changes: SideChanges) -> str | None:
    """Why these files make no new version, or None when they do."""
    if not changes.files.changed:
        return f"Nothing has changed: the {changes.role} side already holds exactly these files."
    if not changes.elements.meaningful:
        count = (
            len(changes.files.added) + len(changes.files.removed)
            + len(changes.files.modified) + len(changes.files.renamed)
        )
        return (
            f"No meaningful change: {count} file(s) differ, but no element the "
            f"analysis compares changed. No new version was made."
        )
    return None


def held_files(latest: ProjectVersion, side: ProjectSource) -> Artifact:
    """The files a side holds at the newest version."""
    return next(artifact for artifact in latest.artifacts if artifact.role == side.role)


def prepare_workspace(
    latest: ProjectVersion,
    side: ProjectSource,
    upload_id: str | None,
    # Where a connected side stands right now, already read by the route.
    head: str | None,
) -> tuple[str, Path, list[dict], list[SyncSourceResult], dict[str, str]]:
    """Assemble the files this update will analyse.

    Built as an ordinary pending upload, so saving it afterwards goes through
    exactly the path a hand-made upload does.
    """
    upload_id_out, workspace = artifact_store.create_upload_dir()
    manifest: list[dict] = []
    reported: list[SyncSourceResult] = []
    renames: dict[str, str] = {}
    sides = {s.role: s for s in latest.config.sources}

    for artifact in latest.artifacts:
        directory = workspace / artifact.role
        held = sides.get(artifact.role)
        refresh = held is not None and held.source_id == side.source_id

        if refresh and upload_id:
            # Handed over by hand: the whole current file set of this side.
            shutil.copytree(staged_dir(upload_id), directory, dirs_exist_ok=True)
            origin, ref = ORIGIN_UPLOAD, None
        elif refresh:
            # Only a commit can be compared with another; an upload's
            # fingerprint names none.
            base = side.checked_ref or (artifact.ref if artifact.origin == ORIGIN_GITHUB else None)
            if head and base:
                renames.update(renames_since(side, base, head))
            origin, ref = ORIGIN_GITHUB, fetch_source(side, directory)
        else:
            entries = [(f.relative_path, f.sha256) for f in artifact.files]
            if not artifact_store.materialise(entries, directory):
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=(
                        f"The stored files for '{artifact.name}' are no longer on "
                        f"disk, so this update has nothing to compare against."
                    ),
                )
            # Carried over as it was, so the new version says the same of it.
            origin, ref = artifact.origin, artifact.ref

        manifest.append({
            "role": artifact.role,
            "artifact_type": artifact.artifact_type,
            "name": held.name if held else artifact.name,
            "directory": artifact.role,
            "origin": origin,
            "ref": ref,
        })
        if held is not None:
            reported.append(SyncSourceResult(
                source_id=held.source_id,
                role=held.role,
                name=held.name,
                kind=held.kind,
                origin=origin,
                refreshed=refresh,
                ref=ref,
            ))

    artifact_store.write_manifest(workspace, manifest)
    return upload_id_out, workspace, manifest, reported, renames


def run_pipeline(
    config: ProjectConfig,
    manifest: list[dict],
    workspace: Path,
    pinned: dict[str, set[str]],
) -> tuple[AnalyzeResponse, float]:
    """Run the analysis over the prepared workspace. Nothing is saved here."""
    sides = {entry["role"]: entry for entry in manifest}
    directories = {role: workspace / entry["directory"] for role, entry in sides.items()}
    settings = config_of(config)

    started = time.perf_counter()
    result = build_pipeline_response(
        source_provider=build_provider(sides[ROLE_SOURCE]["artifact_type"],
                                       directories[ROLE_SOURCE]),
        target_provider=build_provider(sides[ROLE_TARGET]["artifact_type"],
                                       directories[ROLE_TARGET]),
        source_kind=sides[ROLE_SOURCE]["artifact_type"],
        target_kind=sides[ROLE_TARGET]["artifact_type"],
        source_preprocessor=settings.source_preprocessor,
        target_preprocessor=settings.target_preprocessor,
        classifier=settings.classifier,
        n_results=settings.n_results,
        source_output_level=settings.source_output_level,
        target_output_level=settings.target_output_level,
        dependency_expansion_depth=settings.dependency_expansion_depth,
        summarize_elements=settings.summarize_elements,
        chroma_path=get_chroma_path(f"analysis-{config.config_id}"),
        use_persistent_cache=True,
        # The analysis's own collections, holding the previous version's
        # elements. Replacing them is what makes the new state current.
        reset_vector_stores=False,
        pinned_links=pinned,
        workspace_roots=list(directories.values()),
    )
    duration = time.perf_counter() - started
    return relativize_response(result, list(directories.values())), duration


def perform_sync(
    db: Session,
    config: ProjectConfig,
    request: SyncRequest,
    head: str | None,
    job_id: int | None = None,
) -> SyncResponse:
    """Do the work for one side: fetch or take the files, re-run, keep the result."""
    def stage(text: str) -> None:
        if job_id is not None:
            jobs.set_stage(db, job_id, text)

    latest = require_files(db, config)
    previous = service.latest_analysis(db, config)
    side = service.get_side(db, config, request.source_id)

    stage("Fetching files" if request.upload_id is None else "Reading the uploaded files")
    upload_id, workspace, manifest, sources, renames = prepare_workspace(
        latest, side, request.upload_id, head
    )

    try:
        stage("Comparing files")
        changes = side_changes(config, held_files(latest, side), workspace / side.role, renames)
        fetched = next((s.ref for s in sources if s.refreshed and s.origin == ORIGIN_GITHUB), None)
        detail = no_change_detail(changes)
        if detail:
            if fetched:
                side.checked_ref = fetched
                db.commit()
            return SyncResponse(synced=False, detail=detail, changes=changes_response(changes))

        maps = {side.role: changes.elements.id_map}
        source_map, target_map = maps.get(ROLE_SOURCE, IdMap()), maps.get(ROLE_TARGET, IdMap())
        pinned = translate_pins(service.pinned_links(db, config.config_id), source_map, target_map)

        stage("Analysing")
        result, duration = run_pipeline(config, manifest, workspace, pinned)

        version = service.next_version(db, config)
        version.note = (request.note or "").strip() or None
        version.changes_json = json.dumps({side.role: changes.to_dict()})
        if fetched:
            side.checked_ref = fetched
        analysis = service.save_analysis(
            db, config, version.version_id,
            note=request.note, result=result, execution_duration=duration,
        )
        service.claim_artifacts(db, version, upload_id)
        service.keep_pins(db, analysis, result)
        # The same reading of the links the change report gives.
        counts = change_report(
            service.run_view(previous) if previous else None,
            service.run_view(analysis), source_map, target_map,
        )["summary"]
    finally:
        # Claimed by now if the run succeeded; if not, nothing ever will.
        artifact_store.discard_upload(upload_id)
        if request.upload_id:
            artifact_store.discard_upload(request.upload_id)

    return SyncResponse(
        synced=True,
        detail=(
            f"Version {version.version_number}: {side.role} side updated, "
            f"{len(result.trace_links)} trace links."
        ),
        version_id=version.version_id,
        version_number=version.version_number,
        analysis_id=analysis.analysis_id,
        trace_links=len(result.trace_links),
        added=counts["new"],
        removed=counts["no_longer_found"] + counts["broken"],
        compared_with=previous.analysis_id if previous else None,
        sources=sources,
        changes=changes_response(changes),
    )


def run_sync_job(
    job_id: int, config_id: int, user_id: int, request: SyncRequest, head: str | None,
) -> None:
    """The background half of an update. Owns its own session."""
    with SessionLocal() as db:
        jobs.start(db, job_id)
        try:
            config = service.get_config(db, user_id, config_id)
            if config is None:
                raise SyncError("The analysis was removed before the update could run.")
            response = perform_sync(db, config, request, head, job_id)
            jobs.succeed(db, job_id, response.model_dump(mode="json"))
            logger.info(f"Update job {job_id} finished: {response.detail}")
        except Exception as error:
            db.rollback()
            logger.error(f"Update job {job_id} failed: {error}", exc_info=True)
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            jobs.fail(db, job_id, str(detail))
