"""Syncing one pair: assembling its files, re-running its configurations, updating their graphs.

The background half of a sync, and the checks the sync routes share with it.
"""

import logging
import shutil
import time
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from api.capabilities import ROLE_SOURCE, ROLE_TARGET
from api.project_routes import config_of, update_graph
from api.pipeline_factory import build_pipeline_response, build_provider
from api.workspace import get_chroma_path, relativize_response
from api.schemas import (
    AnalyzeResponse,
    SyncConfigResult,
    SyncRequest,
    SyncResponse,
    SyncSourceResult,
)
from core.db.models import Analysis, Project, ProjectConfig
from core.db.session import SessionLocal
from core import jobs
from core.projects import artifact_store, service
from core.sync import SyncError, fetch_source, renames_since

logger = logging.getLogger(__name__)


def require_pair_run(
    db: Session, project: Project, source_kind: str, target_kind: str
) -> Analysis:
    """The last run between two kinds, which is what a sync of them starts from.

    It says which kind sat on which side and holds the files a side nobody
    touched is restored from, so a pair that was never run cannot be synced.
    """
    latest = service.latest_pair_analysis(db, project, source_kind, target_kind)
    if latest is None or not latest.artifacts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This project has no saved run from {source_kind} to {target_kind} "
                f"with stored artifacts, so there is nothing to sync against. Run "
                f"and save an analysis first."
            ),
        )
    return latest


def behind_kinds(db: Session, project: Project, latest: Analysis) -> set[str]:
    """The kinds whose source has moved on since this pair's last run."""
    return {
        artifact.artifact_type
        for artifact in latest.artifacts
        if service.side_is_behind(db, project, artifact)
    }


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


def prepare_workspace(
    db: Session,
    project: Project,
    latest: Analysis,
    refreshed_ids: set[int],
    # What each connected source is at now, already read during the check, so
    # asking GitHub a second time is unnecessary.
    heads: dict[int, str],
    # Sources whose files were handed over rather than fetched, as
    # source_id -> staged upload id.
    replacements: dict[int, str],
) -> tuple[str, Path, list[dict], list[SyncSourceResult], dict[str, str]]:
    """Assemble the artifacts this sync will analyse.

    `latest` is the pair's last run. Both of its sides are filled, not only the
    one that moved: the pipeline compares two complete corpora, so a side
    nobody touched is restored from the blobs already stored rather than
    fetched again.

    Built as an ordinary pending upload, so saving it afterwards goes through
    exactly the path a hand-made upload does - blobs, artifact rows, source
    fingerprints and the sealed version all come for free.
    """
    upload_id, workspace = artifact_store.create_upload_dir()
    manifest: list[dict] = []
    reported: list[SyncSourceResult] = []
    # Files that moved, gathered across every refreshed source, so the graph
    # can follow its elements instead of declaring them lost.
    renames: dict[str, str] = {}

    for artifact in latest.artifacts:
        directory = workspace / artifact.role
        # Where this kind comes from *now*, which is not necessarily where the
        # last run got it: a project that uploaded its code and later connected
        # a repository should be fetched from the repository.
        source = service.active_source_for_kind(db, project, artifact.artifact_type)
        staged = replacements.get(source.source_id) if source else None
        refresh = source is not None and source.source_id in refreshed_ids

        if staged:
            # Handed over by hand. Takes precedence over restoring: these files
            # are the whole point of the sync.
            shutil.copytree(staged_dir(staged), directory, dirs_exist_ok=True)
            ref = None
        elif refresh:
            # Asked before fetching, while the source still remembers where it
            # was: afterwards its ref has moved on and the comparison is lost.
            head = heads.get(source.source_id)
            if head:
                renames.update(renames_since(source, head))
            ref = fetch_source(source, directory)
        else:
            # What the source holds now, which is not always what this pair
            # last read: a kind shared with another pair may have moved on
            # since, and this run is what catches this pair up with it.
            held = service.latest_artifact(db, source) if source else None
            entries = [(f.relative_path, f.sha256) for f in (held or artifact).files]
            if not artifact_store.materialise(entries, directory):
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=(
                        f"The stored files for '{artifact.name}' are no longer on "
                        f"disk, so this sync has nothing to compare against."
                    ),
                )
            ref = source.last_sync_ref if source else None

        manifest.append({
            "role": artifact.role,
            "artifact_type": artifact.artifact_type,
            "name": source.name if source else artifact.name,
            "directory": artifact.role,
            # Names the row this belongs to, so a fetched repository updates
            # its own source instead of being filed as a new upload.
            "source_id": source.source_id if source else None,
            "ref": ref,
        })
        if source is not None:
            reported.append(SyncSourceResult(
                source_id=source.source_id,
                name=source.name,
                kind=source.kind,
                origin=source.origin,
                refreshed=refresh or bool(staged),
                ref=ref,
            ))

    artifact_store.write_manifest(workspace, manifest)
    return upload_id, workspace, manifest, reported, renames


def run_config(
    db: Session,
    project: Project,
    config: ProjectConfig,
    manifest: list[dict],
    workspace: Path,
    version_id: int,
    note: str | None,
) -> tuple[Analysis, AnalyzeResponse]:
    """Run one configuration over the prepared workspace and save the result."""
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
        chroma_path=get_chroma_path(f"project-{project.project_id}"),
        use_persistent_cache=True,
        # This configuration's own collections, holding the previous version's
        # elements. Replacing them is what makes the new state current.
        reset_vector_stores=False,
        # A sync is where the corpus changes, so it is the only place a link
        # can be pushed out of the top-k by code that has nothing to do with
        # it. Offering last run's links back means one can only end on a
        # verdict. Rebased onto this run's workspace first: they are stored as
        # project paths, and the pipeline names its elements absolutely.
        pinned_links=service.pinned_links(db, config.config_id),
        workspace_roots=list(directories.values()),
    )
    duration = time.perf_counter() - started
    result = relativize_response(result, list(directories.values()))

    analysis = service.save_analysis(
        db, project, note=note, config=settings, result=result,
        execution_duration=duration, version_id=version_id,
        source_kind=sides[ROLE_SOURCE]["artifact_type"],
        target_kind=sides[ROLE_TARGET]["artifact_type"],
    )
    return analysis, result


def summarise_config(
    db: Session,
    config: ProjectConfig,
    analysis: Analysis,
    previous: Analysis | None,
    result: AnalyzeResponse,
) -> SyncConfigResult:
    """What this configuration found, and how it differs from its last run.

    Compared against the same configuration only. Two configurations read the
    artifacts differently, so a diff between them would measure the reading
    rather than the change in the artifacts.
    """
    summary = SyncConfigResult(
        config_id=config.config_id,
        config_key=config.config_key,
        analysis_id=analysis.analysis_id,
        trace_links=len(result.trace_links),
    )
    if previous is None:
        return summary

    counts = service.compare_analyses(db, previous, analysis)["summary"]
    summary.added = counts["added"]
    summary.removed = counts["removed"]
    summary.modified = counts["modified"]
    summary.compared_with = previous.analysis_id
    return summary


def pair_configs(db: Session, project: Project, request: SyncRequest) -> list[ProjectConfig]:
    """The configurations a sync re-runs: the pair's own, narrowed if asked."""
    configs = [
        config for config in service.list_configs(db, project)
        if (config.source_kind, config.target_kind)
        == (request.source_kind, request.target_kind)
    ]
    if request.config_ids is not None:
        wanted = set(request.config_ids)
        configs = [config for config in configs if config.config_id in wanted]
    return configs


def perform_sync(
    db: Session,
    project: Project,
    request: SyncRequest,
    refreshed_ids: set[int],
    heads: dict[int, str],
    job_id: int | None = None,
) -> SyncResponse:
    """Do the work for one pair: fetch, re-run its configurations, update their graphs.

    Minutes long, so it is called from a background job rather than from the
    request. `job_id` is only for saying where it has got to.
    """
    def stage(text: str, current: int = 0, total: int = 0) -> None:
        if job_id is not None:
            jobs.set_stage(db, job_id, text, current, total)

    latest = service.latest_pair_analysis(
        db, project, request.source_kind, request.target_kind
    )
    configs = pair_configs(db, project, request)

    stage("Fetching sources")
    upload_id, workspace, manifest, sources, renames = prepare_workspace(
        db, project, latest, refreshed_ids, heads, request.replacements
    )

    version = service.next_version(db, project)
    results: list[SyncConfigResult] = []
    # The run that took the files. Everything after it shares the same blobs,
    # because every configuration analysed the very same bytes.
    holder: Analysis | None = None

    try:
        for number, config in enumerate(configs, start=1):
            stage(f"Analysing with configuration {number} of {len(configs)}", number, len(configs))
            previous = service.latest_analysis(db, project, config.config_id)
            try:
                analysis, result = run_config(
                    db, project, config, manifest, workspace,
                    version.version_id, request.note,
                )
            except Exception as error:
                # One configuration failing must not lose the others' work, or
                # the files this sync already fetched.
                logger.error(f"Sync of config {config.config_key} failed: {error}", exc_info=True)
                results.append(SyncConfigResult(
                    config_id=config.config_id, config_key=config.config_key,
                    error=str(error),
                ))
                continue

            if holder is None:
                # The files stay on disk: the configurations after this one
                # still have to read them.
                service.claim_artifacts(db, analysis, upload_id, keep_files=True)
                holder = analysis
            else:
                service.copy_artifacts(db, holder, analysis)

            update_graph(db, analysis, result, renames)
            results.append(summarise_config(db, config, analysis, previous, result))
    finally:
        # Every configuration has had its turn, so the working files go either
        # way. If one succeeded, its run claimed them and they are stored as
        # blobs. If none did, nothing claimed them: sources keep their old refs
        # and the next sync sees the same work still waiting.
        artifact_store.discard_upload(upload_id)
        if holder is not None:
            # Their contents are stored as blobs now, and an id that could be
            # named again would replay files the project has already taken in.
            for staged in request.replacements.values():
                artifact_store.discard_upload(staged)

    return SyncResponse(
        synced=True,
        detail=(
            f"Version {version.version_number}: {len(refreshed_ids)} source(s) "
            f"refreshed, {len(results)} configuration(s) re-run."
        ),
        version_id=version.version_id,
        version_number=version.version_number,
        sources=sources,
        configs=results,
    )


def run_sync_job(
    job_id: int, project_id: int, user_id: int, request: SyncRequest,
    refreshed_ids: set[int], heads: dict[int, str],
) -> None:
    """The background half of a sync. Owns its own session.

    The request's session is closed by the time this runs - the response has
    already gone out - so nothing from it can be carried in here.
    """
    with SessionLocal() as db:
        jobs.start(db, job_id)
        try:
            project = service.get_project(db, user_id, project_id)
            if project is None:
                raise SyncError("The project was removed before the sync could run.")
            response = perform_sync(db, project, request, refreshed_ids, heads, job_id)
            jobs.succeed(db, job_id, response.model_dump(mode="json"))
            logger.info(f"Sync job {job_id} finished: {response.detail}")
        except Exception as error:
            logger.error(f"Sync job {job_id} failed: {error}", exc_info=True)
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            jobs.fail(db, job_id, str(detail))
