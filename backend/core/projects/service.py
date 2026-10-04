"""Projects, analyses and their runs, always scoped to an owner.

An analysis (a ProjectConfig row) owns its two sides, its versions, its graph
and its pinned links. A version owns both sides' files, and every run of that
version reads them. Nothing here is shared between two analyses, so nothing
here ever has to decide which of several analyses a file or a version belongs
to. Only the bytes are shared, through the content-addressed blob store.
"""

import json
import logging
from hashlib import sha256

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core import secrets
from core.db.models import (
    Analysis, Artifact, ArtifactFile, ElementLink, Project,
    ProjectConfig, ProjectSource, ProjectVersion, TraceLink, utcnow,
)
from core.projects import artifact_store
from core.output.names import display_names, model_name
from core.projects.diff import IdMap, element_hash
from core.projects.report import RunView, change_report
from core.projects.run_config import expansion_depth

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 50

# Where a side's files come from, and so whether an update can fetch them on
# its own or has to ask the user for them again.
ORIGIN_UPLOAD = "upload"
ORIGIN_GITHUB = "github"


# ----- Projects -----

def create_project(db: Session, user_id: int, name: str, description: str | None) -> Project:
    project = Project(
        user_id=user_id,
        project_name=name.strip(),
        description=(description or "").strip() or None,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_project(db: Session, user_id: int, project_id: int) -> Project | None:
    # Missing and not-yours give the same answer, so the response cannot be
    # used to discover which ids exist.
    return db.scalar(
        select(Project).where(Project.project_id == project_id, Project.user_id == user_id)
    )


def list_projects(db: Session, user_id: int) -> list[tuple[Project, int]]:
    """Every project of this user, each with how many analyses it holds."""
    rows = db.execute(
        select(Project, func.count(ProjectConfig.config_id))
        .outerjoin(ProjectConfig, ProjectConfig.project_id == Project.project_id)
        .where(Project.user_id == user_id)
        .group_by(Project.project_id)
        .order_by(Project.updated_at.desc())
    ).all()
    return [(project, count) for project, count in rows]


def delete_project(db: Session, project: Project) -> None:
    db.delete(project)
    db.commit()
    # After the rows are gone, so the sweep sees the truth. Blobs still shared
    # with another analysis survive.
    collect_garbage(db)


# ----- Analyses: the relation, its settings and its two sides -----

def create_config(
    db: Session,
    project: Project,
    config,
    source_kind: str | None,
    target_kind: str | None,
) -> ProjectConfig:
    """A new analysis, with these settings between these two kinds.

    Always a new row, never one looked up by its settings: running the same
    settings again over other files is a second analysis with its own
    history, not more history for the first.

    The kinds are given beside the settings rather than inside them: they are
    a fact about the artifacts a run was pointed at, not something it chose.
    """
    depth = expansion_depth(target_kind, config.dependency_expansion_depth)
    created = ProjectConfig(
        project_id=project.project_id,
        source_kind=source_kind,
        target_kind=target_kind,
        source_preprocessor=config.source_preprocessor.value,
        target_preprocessor=config.target_preprocessor.value,
        source_output_level=(
            config.source_output_level.value if config.source_output_level else None
        ),
        target_output_level=(
            config.target_output_level.value if config.target_output_level else None
        ),
        top_k=config.n_results,
        dependency_expansion_depth=depth,
        classifier_type=config.classifier.value,
        summarize_elements=config.summarize_elements,
    )
    db.add(created)
    # Flushed, not committed: the run being saved needs the id, and both rows
    # belong to the same save.
    db.flush()
    return created


def get_config(db: Session, user_id: int, config_id: int) -> ProjectConfig | None:
    """One analysis, if it belongs to this user."""
    return db.scalar(
        select(ProjectConfig)
        .join(Project, Project.project_id == ProjectConfig.project_id)
        .where(ProjectConfig.config_id == config_id, Project.user_id == user_id)
    )


def list_configs(db: Session, project: Project) -> list[ProjectConfig]:
    """Every analysis in this project, oldest first."""
    return list(db.execute(
        select(ProjectConfig)
        .where(ProjectConfig.project_id == project.project_id)
        .order_by(ProjectConfig.config_id)
    ).scalars())


def list_user_configs(
    db: Session, user_id: int, project_id: int | None = None
) -> list[ProjectConfig]:
    """Every analysis this user has, newest first, optionally in one project."""
    query = (
        select(ProjectConfig)
        .join(Project, Project.project_id == ProjectConfig.project_id)
        .where(Project.user_id == user_id)
        .order_by(ProjectConfig.created_at.desc(), ProjectConfig.config_id.desc())
    )
    if project_id is not None:
        query = query.where(ProjectConfig.project_id == project_id)
    return list(db.execute(query).scalars())


def delete_config(db: Session, config: ProjectConfig) -> None:
    """Remove an analysis with everything it owns: sides, versions, runs, graph."""
    db.delete(config)
    db.commit()
    collect_garbage(db)


def source_fingerprint(files: list[ArtifactFile]) -> str:
    """A name for exactly this set of file contents.

    Lets a re-upload of unchanged files be recognised as unchanged, which is
    what stops an identical upload being treated as something new to analyse.
    """
    parts = sorted(f"{file.relative_path}:{file.sha256}" for file in files)
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def get_side(db: Session, config: ProjectConfig, source_id: int) -> ProjectSource | None:
    """One of this analysis's two sides, by id."""
    return db.execute(
        select(ProjectSource).where(
            ProjectSource.source_id == source_id,
            ProjectSource.config_id == config.config_id,
        )
    ).scalar_one_or_none()


def side_for_role(db: Session, config: ProjectConfig, role: str) -> ProjectSource | None:
    return db.execute(
        select(ProjectSource).where(
            ProjectSource.config_id == config.config_id,
            ProjectSource.role == role,
        )
    ).scalar_one_or_none()


def latest_artifact(db: Session, side: ProjectSource) -> Artifact | None:
    """The files a side holds now: its files in the analysis's newest version."""
    return db.execute(
        select(Artifact)
        .join(ProjectVersion, Artifact.version_id == ProjectVersion.version_id)
        .where(ProjectVersion.config_id == side.config_id, Artifact.role == side.role)
        .order_by(ProjectVersion.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_sides(db: Session, config: ProjectConfig) -> list[ProjectSource]:
    """The analysis's sides, source first."""
    sides = db.execute(
        select(ProjectSource).where(ProjectSource.config_id == config.config_id)
    ).scalars().all()
    # "source" sorts after "target" alphabetically, so the order is spelled out.
    return sorted(sides, key=lambda side: side.role != "source")


def connect_github_side(
    db: Session,
    side: ProjectSource,
    repository: str,
    branch: str,
    token: str | None = None,
) -> ProjectSource:
    """Point one side of an analysis at a GitHub repository.

    The side keeps its files until the next update fetches the repository.
    Nothing it holds was fetched from there, so the first check always reads
    as a change.
    """
    if side.location != repository or side.branch != branch:
        side.checked_ref = None
    side.origin = ORIGIN_GITHUB
    side.location = repository
    side.branch = branch
    side.name = repository.split("/")[-1]
    # Only when a new one was given: reconnecting to change the branch must
    # not silently drop the token that made the repository readable.
    if token:
        side.access_token = secrets.encrypt(token)
    db.commit()
    db.refresh(side)
    return side


# ----- Versions -----

def next_version(db: Session, config: ProjectConfig) -> ProjectVersion:
    """Start the analysis's next version.

    A version is one state of the analysis's files. New files are what begins
    one - so a New Analysis opens version 1, and everything run against those
    same files afterwards belongs to it.
    """
    highest = db.execute(
        select(func.max(ProjectVersion.version_number))
        .where(ProjectVersion.config_id == config.config_id)
    ).scalar()

    created = ProjectVersion(
        config_id=config.config_id,
        version_number=(highest or 0) + 1,
    )
    db.add(created)
    # Flushed rather than committed: the run being saved needs the id, and
    # both rows belong to the same save.
    db.flush()
    return created


def list_versions(db: Session, config: ProjectConfig) -> list[tuple[ProjectVersion, int]]:
    """Every state this analysis's files have been in, newest first.

    Paired with how many runs were made against each.
    """
    counted = (
        select(Analysis.version_id, func.count().label("runs"))
        .where(Analysis.config_id == config.config_id)
        .group_by(Analysis.version_id)
        .subquery()
    )
    rows = db.execute(
        select(ProjectVersion, func.coalesce(counted.c.runs, 0))
        .outerjoin(counted, counted.c.version_id == ProjectVersion.version_id)
        .where(ProjectVersion.config_id == config.config_id)
        .order_by(ProjectVersion.version_number.desc())
    ).all()
    return [(version, runs) for version, runs in rows]


def latest_version(db: Session, config: ProjectConfig) -> ProjectVersion | None:
    return db.execute(
        select(ProjectVersion)
        .where(ProjectVersion.config_id == config.config_id)
        .order_by(ProjectVersion.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def version_numbers(db: Session, version_ids) -> dict[int, int]:
    """The number each version is known by, for a set of version ids.

    Looked up in one query for a whole page of runs. A run stores the version
    it belongs to, but the user only ever sees the number - so without this a
    run cannot be matched to the state of the files it ran against.
    """
    wanted = {version_id for version_id in version_ids if version_id is not None}
    if not wanted:
        return {}

    return dict(db.execute(
        select(ProjectVersion.version_id, ProjectVersion.version_number)
        .where(ProjectVersion.version_id.in_(wanted))
    ).all())


def count_runs(db: Session, config: ProjectConfig) -> int:
    """How many runs this analysis has, across all its versions."""
    return db.execute(
        select(func.count()).select_from(Analysis).where(Analysis.config_id == config.config_id)
    ).scalar_one()


# ----- Pinned links -----

def pinned_links(db: Session, config_id: int) -> dict[str, set[str]]:
    """Last run's links for this analysis, as source -> targets.

    Handed to the next run so the classifier is asked about them again. Without
    it a link can disappear because a grown corpus pushed it out of the top-k,
    which reads exactly like the classifier having changed its mind while
    nothing actually decided anything.
    """
    grouped: dict[str, set[str]] = {}
    for source, target in db.execute(
        select(ElementLink.source_identifier, ElementLink.target_identifier)
        .where(ElementLink.config_id == config_id)
    ):
        grouped.setdefault(source, set()).add(target)
    return grouped


def record_element_links(
    db: Session, config_id: int, links: list | None
) -> int:
    """Keep the pairs this run's classifier judged, for the next run to re-offer.

    Replaced wholesale rather than merged: what is stored is always the most
    recent run's pairs and only those. Merging would keep proposing pairs that
    stopped being relevant several updates ago.

    An empty list and no list at all mean different things, so they are treated
    differently. `[]` is a run saying it linked nothing, which clears the store.
    `None` is a caller that has nothing to say - a result shape from before
    this existed - and leaves what is stored alone rather than discarding pins
    on the word of something that was never asked the question.
    """
    if links is None:
        logger.info(f"Pinned links for config {config_id}: none given, stored ones left as they are")
        return 0

    removed = db.execute(
        delete(ElementLink).where(ElementLink.config_id == config_id)
    ).rowcount
    if not links:
        # Said out loud, because storing nothing is what makes the next update
        # unable to carry a link over - and it looks like nothing happened.
        logger.info(
            f"Pinned links for config {config_id}: stored 0, the run handed over "
            f"no element-level links ({removed} stored before were cleared)"
        )
        return 0
    # Deduplicated because dependency expansion and aggregation can both hand
    # back the same pair, and the table holds one row per pair.
    unique = {(source, target) for source, target in links}
    db.add_all([
        ElementLink(config_id=config_id, source_identifier=source, target_identifier=target)
        for source, target in unique
    ])

    logger.info(
        f"Pinned links for config {config_id}: stored {len(unique)} "
        f"({removed} stored before were replaced)"
    )
    return len(unique)


def keep_pins(db: Session, analysis: Analysis, result) -> None:
    """Keep the pairs this run's classifier confirmed, for the next run to re-offer."""
    record_element_links(db, analysis.config_id, getattr(result, "element_links", None))
    db.commit()


# ----- Runs -----

def save_analysis(
    db: Session,
    config: ProjectConfig,
    version_id: int,
    note: str | None,
    result,
    execution_duration: float | None,
) -> Analysis:
    """Store one run of an analysis, against one of its versions.

    The version is given by the caller, because only it knows whether these
    are new files or the stored ones a re-run reaches for.
    """
    analysis = Analysis(
        project_id=config.project_id,
        config_id=config.config_id,
        version_id=version_id,
        note=(note or "").strip() or None,
        execution_duration=execution_duration,
        snapshot_json=json.dumps({
            "source_elements": [e.model_dump() for e in result.source_elements],
            "target_elements": [e.model_dump() for e in result.target_elements],
            "unimplemented": result.unimplemented,
            "summary": result.summary,
        }),
    )
    analysis.trace_links = [
        TraceLink(
            source_id=link.source_id,
            source_content=link.source_content,
            target_id=link.target_id,
            target_content=link.target_content,
            similarity_score=link.confidence,
            confidence_level=link.confidence_level,
            explanation=link.explanation,
        )
        for link in result.trace_links
    ]

    db.add(analysis)
    # Moves the project to the top of the "last activity" ordering.
    config.project.updated_at = utcnow()
    db.commit()
    db.refresh(analysis)
    return analysis


def latest_analysis(db: Session, config: ProjectConfig) -> Analysis | None:
    """The analysis's most recent run."""
    return db.execute(
        select(Analysis)
        .where(Analysis.config_id == config.config_id)
        .order_by(Analysis.created_at.desc(), Analysis.analysis_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _side_for_entry(db: Session, config: ProjectConfig, entry: dict) -> ProjectSource:
    """The side a stored file set belongs to, made on first sight.

    A side is one end of the trace, so its role is all that identifies it. An
    upload under a new name is the same side holding new files.
    """
    role = entry.get("role") or "source"
    side = side_for_role(db, config, role)
    if side is None:
        side = ProjectSource(
            config_id=config.config_id,
            role=role,
            kind=entry.get("artifact_type") or "unknown",
            name=entry.get("name") or "artifact",
            origin=ORIGIN_UPLOAD,
        )
        db.add(side)
        db.flush()
    elif side.origin == ORIGIN_UPLOAD and entry.get("name"):
        side.name = entry["name"]
    return side


def claim_artifacts(db: Session, version: ProjectVersion, upload_id: str | None) -> int:
    """Take an upload's files into the blob store as this version's file sets.

    Returns how many sides were stored. Each side is stored complete, so a
    version always says everything its runs read - even the side nobody
    changed, whose rows simply point at the blobs already there.
    """
    # Zero is normal - no upload, aged out, or already claimed - so this never
    # raises and the run is kept either way.
    if not upload_id:
        return 0

    pending = artifact_store.upload_dir(upload_id)
    if pending is None or not pending.is_dir():
        return 0

    attached = 0
    for entry in artifact_store.read_manifest(pending):
        files = artifact_store.collect_files(pending / entry.get("directory", ""))
        if not files:
            continue

        artifact = Artifact(
            version_id=version.version_id,
            name=entry.get("name") or "artifact",
            artifact_type=entry.get("artifact_type") or "unknown",
            role=entry.get("role") or "source",
            origin=entry.get("origin") or ORIGIN_UPLOAD,
        )

        total = 0
        for relative_path, path in files:
            digest, size, _existed = artifact_store.store_blob(path)
            total += size
            artifact.files.append(ArtifactFile(
                relative_path=relative_path, sha256=digest, byte_size=size
            ))

        artifact.file_count = len(files)
        artifact.byte_size = total
        # A fetched side records the commit it came from, because that is what
        # the next check compares against. An upload has no such name, so its
        # contents are fingerprinted instead.
        artifact.ref = entry.get("ref") or source_fingerprint(artifact.files)

        _side_for_entry(db, version.config, entry)
        db.add(artifact)
        attached += 1

    db.commit()
    # Only once every blob is referenced; dropping the upload before the commit
    # would risk losing the files.
    artifact_store.discard_upload(upload_id)
    return attached


def collect_garbage(db: Session) -> int:
    """Retire blobs no artifact references any more."""
    return artifact_store.collect_garbage(
        set(db.scalars(select(ArtifactFile.sha256).distinct()))
    )


def analyses_with_files(db: Session, analysis_ids: list[int]) -> set[int]:
    """Which of these analyses still have every one of their files on disk.

    Answered here rather than when a re-run or a download is attempted, so a
    run whose bytes are gone can say so in the list instead of after a form
    has been filled in.
    """
    if not analysis_ids:
        return set()

    rows = db.execute(
        select(Analysis.analysis_id, ArtifactFile.sha256)
        .join(Artifact, Artifact.version_id == Analysis.version_id)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.artifact_id)
        .where(Analysis.analysis_id.in_(analysis_ids))
    ).all()

    # One filesystem check per distinct digest, not per row: the runs of one
    # version share its files, so the same digest appears under several runs.
    present = {digest: artifact_store.blob_exists(digest) for _, digest in rows}

    holders = {analysis_id for analysis_id, _ in rows}
    incomplete = {analysis_id for analysis_id, digest in rows if not present[digest]}
    return holders - incomplete


def get_analysis(db: Session, user_id: int, analysis_id: int) -> Analysis | None:
    return db.scalar(
        select(Analysis)
        .join(Project, Project.project_id == Analysis.project_id)
        .where(Analysis.analysis_id == analysis_id, Project.user_id == user_id)
    )


def _link_count():
    # Counted as subqueries: two counts as joins would fan out against each
    # other and inflate both.
    return (
        select(func.count(TraceLink.trace_id))
        .where(TraceLink.analysis_id == Analysis.analysis_id)
        .correlate(Analysis)
        .scalar_subquery()
    )


def _artifact_count():
    # A run's files are its version's, shared with every other run of it.
    return (
        select(func.count(Artifact.artifact_id))
        .where(Artifact.version_id == Analysis.version_id)
        .correlate(Analysis)
        .scalar_subquery()
    )


def list_analyses(
    db: Session,
    user_id: int,
    project_id: int | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[tuple[Analysis, int, int, str]]:
    """Recent runs as (run, link count, artifact count, project name)."""
    query = (
        select(
            Analysis,
            _link_count(),
            _artifact_count(),
            Project.project_name,
        )
        .join(Project, Project.project_id == Analysis.project_id)
        .where(Project.user_id == user_id)
        .order_by(Analysis.created_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        query = query.where(Analysis.project_id == project_id)

    return [tuple(row) for row in db.execute(query).all()]


def delete_analysis(db: Session, analysis: Analysis) -> None:
    """Delete one run. Its version, and the files the version holds, stay."""
    db.delete(analysis)
    db.commit()


def load_snapshot(analysis: Analysis) -> dict:
    try:
        return json.loads(analysis.snapshot_json) or {}
    except (TypeError, ValueError):
        return {}


# ----- The change report -----

class ReportError(ValueError):
    """A change report that cannot be made. The message is safe to show a user."""


def run_view(analysis: Analysis) -> RunView:
    """One run, as the change report reads it."""
    snapshot = load_snapshot(analysis)
    names = {}
    for side in ("source_elements", "target_elements"):
        names.update(display_names([
            (element["identifier"], model_name(element.get("level"), element.get("model_units")))
            for element in snapshot.get(side, [])
        ]))

    def shown(identifier: str) -> str | None:
        return names.get(identifier) if names.get(identifier) != identifier else None

    return RunView(
        links={
            (link.source_id, link.target_id): {
                "confidence": link.similarity_score,
                "confidence_level": link.confidence_level,
                "explanation": link.explanation,
                "source_content": link.source_content,
                "target_content": link.target_content,
                "source_name": shown(link.source_id),
                "target_name": shown(link.target_id),
            }
            for link in analysis.trace_links
        },
        source_hashes={
            element["identifier"]: element_hash(element["content"])
            for element in snapshot.get("source_elements", [])
        },
        target_hashes={
            element["identifier"]: element_hash(element["content"])
            for element in snapshot.get("target_elements", [])
        },
        uncovered=[
            item["identifier"] for item in snapshot.get("unimplemented", []) if item.get("identifier")
        ],
        names={identifier: name for identifier, name in names.items() if name != identifier},
    )


def maps_between(versions: list[ProjectVersion]) -> tuple[IdMap, IdMap]:
    """The id maps that take each side's identifiers from the first version to the last.

    Each version stores the map from the version before it; a report across
    several versions follows them in turn.
    """
    source, target = IdMap(), IdMap()
    for version in versions[1:]:
        changes = json.loads(version.changes_json or "{}")
        source = source.then(IdMap.from_dict(changes.get("source", {}).get("id_map")))
        target = target.then(IdMap.from_dict(changes.get("target", {}).get("id_map")))
    return source, target


def run_count(db: Session, version_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Analysis).where(Analysis.version_id == version_id))


def latest_run(db: Session, version: ProjectVersion) -> Analysis | None:
    """The newest run of one version: what the version's links are now."""
    return db.execute(
        select(Analysis)
        .where(Analysis.version_id == version.version_id)
        .order_by(Analysis.created_at.desc(), Analysis.analysis_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def version_report(
    db: Session,
    config: ProjectConfig,
    base_number: int | None = None,
    head_number: int | None = None,
) -> dict:
    """The change report between two versions of an analysis.

    The later version defaults to the newest and the earlier one to the
    nearest version before it that has a run. Each version is read through
    its newest run, so a re-run inside a version is what the report shows for
    it. A version with no run is not an error: `no_run_version` names it, and
    the later version's links are returned as they are, uncompared - or none,
    when the later version is the one without a run.
    """
    versions = sorted(config.versions, key=lambda version: version.version_number)
    if not versions:
        raise ReportError("This analysis has no version yet.")
    by_number = {version.version_number: version for version in versions}

    head = by_number.get(head_number) if head_number is not None else versions[-1]
    if head is None:
        raise ReportError(f"Version {head_number} does not exist.")
    if base_number is None:
        earlier = [v for v in versions if v.version_number < head.version_number]
        # The nearest one with a run; failing that, the one just before, so
        # the files can still be compared.
        base = next((v for v in reversed(earlier) if latest_run(db, v)), earlier[-1] if earlier else None)
    else:
        base = by_number.get(base_number)
        if base is None:
            raise ReportError(f"Version {base_number} does not exist.")
        if base.version_number >= head.version_number:
            raise ReportError("The earlier version must come before the later one.")

    head_run = latest_run(db, head)
    base_run = latest_run(db, base) if base else None
    missing = head if head_run is None else base if base is not None and base_run is None else None

    span = [v for v in versions if base and base.version_number <= v.version_number <= head.version_number]
    source_map, target_map = maps_between(span)
    head_view = run_view(head_run) if head_run else RunView(links={}, source_hashes={}, target_hashes={}, uncovered=[])
    report = change_report(run_view(base_run) if base_run else None, head_view, source_map, target_map)
    return {
        "base_version": base.version_number if base else None,
        "head_version": head.version_number,
        "base_analysis_id": base_run.analysis_id if base_run else None,
        "head_analysis_id": head_run.analysis_id if head_run else None,
        "no_run_version": missing.version_number if missing else None,
        # What changed in the files, version by version, between the two.
        "versions": span[1:],
        **report,
        "uncovered_names": {
            identifier: head_view.names[identifier]
            for identifier in report["uncovered"] if identifier in head_view.names
        },
    }
