"""Project and saved-analysis persistence, always scoped to an owner."""

import json
import logging
from hashlib import sha256

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session, aliased

from core import secrets
from core.content import content_hash
from core.db.models import (
    Analysis, Artifact, ArtifactFile, ElementLink, GraphEdge, GraphNode, Project,
    ProjectConfig, ProjectSource, ProjectVersion, TraceLink, VersionSource, utcnow,
)
from core.projects import artifact_store
from core.projects.run_config import config_key, expansion_depth

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 50

# Where a source's files come from, and so whether a sync can fetch them on its
# own or has to ask the user for them again.
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
        select(Project, func.count(Analysis.analysis_id))
        .outerjoin(Analysis, Analysis.project_id == Project.project_id)
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


# ----- Sources -----

def source_fingerprint(files: list[ArtifactFile]) -> str:
    """A name for exactly this set of file contents.

    Lets a re-upload of unchanged files be recognised as unchanged, which is
    what stops an identical upload being treated as something new to analyse.
    """
    parts = sorted(f"{file.relative_path}:{file.sha256}" for file in files)
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def get_or_create_source(
    db: Session,
    project: Project,
    kind: str,
    name: str,
    origin: str = ORIGIN_UPLOAD,
) -> ProjectSource:
    """The project's row for one artifact set it can refresh.

    Identified by kind and name together, not by kind alone: both sides of a
    trace may hold the same kind - an old requirements folder traced against a
    new one - and those are two artifact sets, not one seen twice.

    Renaming an uploaded folder therefore reads as a new source. That is the
    cost of having no address to recognise an upload by; a connected source has
    one, and is matched on it instead.
    """
    existing = db.execute(
        select(ProjectSource).where(
            ProjectSource.project_id == project.project_id,
            ProjectSource.kind == kind,
            ProjectSource.name == name,
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Uploading into a disconnected source is asking for it back.
        existing.is_active = True
        return existing

    created = ProjectSource(
        project_id=project.project_id,
        kind=kind,
        name=name,
        origin=origin,
    )
    db.add(created)
    # Flushed rather than committed: the artifact being stored needs the id,
    # and both rows belong to the same claim.
    db.flush()
    return created


def connect_github_source(
    db: Session,
    project: Project,
    kind: str,
    repository: str,
    branch: str,
    name: str,
    token: str | None = None,
) -> ProjectSource:
    """Point one of the project's artifact sets at a GitHub repository.

    Recognised by its address rather than its name, which is what an upload
    could not offer: renaming this source, or moving it to another branch,
    leaves it the same source pointed somewhere new.

    No fingerprint is written here. Nothing has been fetched yet, and a source
    that claims to have been synced when it has not would make the first sync
    believe there was nothing to do.
    """
    existing = db.execute(
        select(ProjectSource).where(
            ProjectSource.project_id == project.project_id,
            ProjectSource.kind == kind,
            ProjectSource.origin == ORIGIN_GITHUB,
            ProjectSource.location == repository,
        )
    ).scalar_one_or_none()

    stored_token = secrets.encrypt(token) if token else None

    if existing is not None:
        existing.branch = branch
        existing.name = name
        # Connecting a repository that was disconnected is asking for it back.
        existing.is_active = True
        # Only when a new one was given: reconnecting to change the branch must
        # not silently drop the token that made the repository readable.
        if stored_token is not None:
            existing.access_token = stored_token
        connected = existing
    else:
        connected = ProjectSource(
            project_id=project.project_id,
            kind=kind,
            name=name,
            origin=ORIGIN_GITHUB,
            location=repository,
            branch=branch,
            access_token=stored_token,
        )
        db.add(connected)
        db.flush()

    _take_over_kind(db, project, kind, connected)
    db.commit()
    db.refresh(connected)
    return connected


def _take_over_kind(
    db: Session,
    project: Project,
    kind: str,
    connected: ProjectSource,
    beside: set[int] = frozenset(),
) -> None:
    """Make this the only place the project takes `kind` from.

    Connecting a repository for the code is saying that is where the code comes
    from now. Whatever it used to be uploaded from is stood down rather than
    left beside it, or a sync would have two candidates and no way to choose.

    Stood down, not deleted: the versions that recorded it still need it to say
    what the artifacts were at the time.

    `beside` names sources that arrived together with this one and stay. Only
    one upload can do that: a trace with the same kind on both sides.
    """
    superseded = db.execute(
        select(ProjectSource).where(
            ProjectSource.project_id == project.project_id,
            ProjectSource.kind == kind,
            ProjectSource.is_active.is_(True),
            ProjectSource.source_id != connected.source_id,
        )
    ).scalars().all()

    for source in superseded:
        if source.source_id in beside:
            continue
        source.is_active = False
        logger.info(
            f"'{source.name}' no longer supplies {kind} for project "
            f"{project.project_id}; '{connected.name}' does."
        )


def active_source_for_kind(db: Session, project: Project, kind: str) -> ProjectSource | None:
    """Where the project currently takes one kind of artifact from.

    None when there is nothing to take it from, and also when there is more
    than one candidate: guessing which repository the code comes from would be
    worse than leaving the files where the last run stored them.
    """
    rows = db.execute(
        select(ProjectSource).where(
            ProjectSource.project_id == project.project_id,
            ProjectSource.kind == kind,
            ProjectSource.is_active.is_(True),
        )
    ).scalars().all()

    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        logger.warning(
            f"Project {project.project_id} has {len(rows)} connected sources of "
            f"kind '{kind}', so none of them can be assumed to be the one to "
            f"refresh. Disconnect the ones that are no longer used."
        )
    return None


def list_sources(
    db: Session, project: Project, include_disconnected: bool = False
) -> list[ProjectSource]:
    """Everything this project holds, oldest first.

    Disconnected sources are left out unless asked for: they are kept so past
    versions stay readable, not because there is anything left to sync.
    """
    query = select(ProjectSource).where(ProjectSource.project_id == project.project_id)
    if not include_disconnected:
        query = query.where(ProjectSource.is_active.is_(True))
    return list(db.execute(query.order_by(ProjectSource.source_id)).scalars())


def disconnect_source(db: Session, source: ProjectSource) -> bool:
    """Stop syncing a source. Returns whether the row was removed outright.

    A source no version ever recorded is a mistake being undone - a mistyped
    repository, a connection never used - and nothing is lost by deleting it.

    Once a version has recorded it, the row is kept and only deactivated. That
    record is what says the code moved between two versions; remove it and both
    versions read as though nothing ever changed.
    """
    in_use = db.execute(
        select(VersionSource.version_id)
        .where(VersionSource.source_id == source.source_id)
        .limit(1)
    ).first()

    if in_use is None:
        db.delete(source)
        db.commit()
        return True

    source.is_active = False
    db.commit()
    return False


def reconnect_source(db: Session, project: Project, source: ProjectSource) -> None:
    """Bring a disconnected source back as where its kind comes from.

    Whatever took its place in the meantime is stood down, the same as when a
    repository is connected: a kind has one source at a time.
    """
    source.is_active = True
    _take_over_kind(db, project, source.kind, source)
    db.commit()


def versions_using(db: Session, source: ProjectSource) -> int:
    """How many versions recorded this source. What disconnecting would cost."""
    return db.execute(
        select(func.count())
        .select_from(VersionSource)
        .where(VersionSource.source_id == source.source_id)
    ).scalar_one()


def get_source(db: Session, project: Project, source_id: int) -> ProjectSource | None:
    return db.execute(
        select(ProjectSource).where(
            ProjectSource.source_id == source_id,
            ProjectSource.project_id == project.project_id,
        )
    ).scalar_one_or_none()


# ----- Configurations -----

def get_or_create_config(
    db: Session,
    project: Project,
    config,
    source_kind: str | None,
    target_kind: str | None,
) -> ProjectConfig:
    """The project's row for this way of reading its artifacts.

    A configuration is not a property of one run: several runs share it, and
    the ones that do are the only ones that can be compared with each other.
    Recognised by its key, so repeating a configuration re-uses its row rather
    than filing a second copy of the same settings.

    The kinds are given beside the settings rather than inside them: they are
    a fact about the artifacts a run was pointed at, not something it chose.
    """
    depth = expansion_depth(target_kind, config.dependency_expansion_depth)
    key = config_key(
        source_kind=source_kind,
        target_kind=target_kind,
        source_preprocessor=config.source_preprocessor,
        target_preprocessor=config.target_preprocessor,
        source_output_level=config.source_output_level,
        target_output_level=config.target_output_level,
        classifier=config.classifier,
        n_results=config.n_results,
        dependency_expansion_depth=depth,
        summarize_elements=config.summarize_elements,
    )

    existing = db.execute(
        select(ProjectConfig).where(
            ProjectConfig.project_id == project.project_id,
            ProjectConfig.config_key == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    created = ProjectConfig(
        project_id=project.project_id,
        config_key=key,
        # Never chosen here: which configuration a project opens on is the
        # user's call, not an accident of which one they happened to run first.
        is_default=False,
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
    # Flushed, not committed: the analysis being saved needs the id, and both
    # rows belong to the same save.
    db.flush()
    return created


# ----- Versions -----

def next_version(db: Session, project: Project) -> ProjectVersion:
    """Start the project's next version.

    A version is one state of the project's artifacts. New artifacts are what
    begins one - so an upload opens a version, and everything run against those
    same files afterwards belongs to it, however many configurations are tried.
    """
    highest = db.execute(
        select(func.max(ProjectVersion.version_number))
        .where(ProjectVersion.project_id == project.project_id)
    ).scalar()

    created = ProjectVersion(
        project_id=project.project_id,
        version_number=(highest or 0) + 1,
    )
    db.add(created)
    # Flushed rather than committed: the analysis being saved needs the id, and
    # both rows belong to the same save.
    db.flush()
    return created


def seal_version(db: Session, version_id: int, project: Project) -> None:
    """Record what every source was at this version.

    Read from the sources themselves rather than from what was just uploaded,
    so a source nobody refreshed keeps the state it already had. That is what
    makes a version a complete picture instead of only its changed part - and
    it is what a later sync compares against to see what moved.
    """
    # Written last, so re-recording a version replaces it rather than
    # colliding with the rows already there.
    db.execute(delete(VersionSource).where(VersionSource.version_id == version_id))
    for source in list_sources(db, project):
        db.add(VersionSource(
            version_id=version_id,
            source_id=source.source_id,
            ref=source.last_sync_ref or "",
        ))


def list_versions(db: Session, project: Project) -> list[tuple[ProjectVersion, int]]:
    """Every state this project's artifacts have been in, newest first.

    Paired with how many runs were made against each, since a version with no
    analysis is one whose sync never produced anything to compare.
    """
    counted = (
        select(Analysis.version_id, func.count().label("runs"))
        .where(Analysis.project_id == project.project_id)
        .group_by(Analysis.version_id)
        .subquery()
    )
    rows = db.execute(
        select(ProjectVersion, func.coalesce(counted.c.runs, 0))
        .outerjoin(counted, counted.c.version_id == ProjectVersion.version_id)
        .where(ProjectVersion.project_id == project.project_id)
        .order_by(ProjectVersion.version_number.desc())
    ).all()
    return [(version, runs) for version, runs in rows]


def count_analyses_by_config(db: Session, project: Project) -> dict[int, int]:
    """How many runs each configuration has, keyed by config id."""
    rows = db.execute(
        select(Analysis.config_id, func.count())
        .where(Analysis.project_id == project.project_id, Analysis.config_id.is_not(None))
        .group_by(Analysis.config_id)
    ).all()
    return {config_id: count for config_id, count in rows}


# ----- The living graph -----

# An edge the latest run confirmed, one it stopped finding, and one whose
# element is gone. Only the last is a fault worth showing a user.
STATUS_ACTIVE = "active"
STATUS_STALE = "stale"
STATUS_BROKEN = "broken"


def apply_renames(nodes: dict, renames: dict[str, str]) -> int:
    """Move stored nodes onto the paths their files now have.

    An element is identified by where it lives - `src/Pay.java::Pay::charge()`.
    Move the file and every identifier under it changes, so the diff that
    follows would see the whole file deleted and an identical one created, and
    report every link on it as broken.

    Rewriting first means the comparison sees what actually happened: the same
    elements, in a new place. Returns how many were moved.
    """
    moved = 0
    for (kind, identifier), node in list(nodes.items()):
        for old_path, new_path in renames.items():
            if identifier != old_path and not identifier.startswith(f"{old_path}::"):
                continue

            renamed = new_path + identifier[len(old_path):]
            # Something already sits there - two files merged into one, say.
            # Leave both to the ordinary diff rather than colliding.
            if (kind, renamed) in nodes:
                break

            del nodes[(kind, identifier)]
            node.identifier = renamed
            if node.parent_identifier and node.parent_identifier.startswith(old_path):
                node.parent_identifier = new_path + node.parent_identifier[len(old_path):]
            nodes[(kind, renamed)] = node
            moved += 1
            break
    return moved


def update_graph(
    db: Session,
    analysis: Analysis,
    result,
    source_kind: str | None,
    target_kind: str | None,
    renames: dict[str, str] | None = None,
) -> None:
    """Bring one configuration's graph up to date with what a run found.

    A saved analysis is what one run saw and never changes; the graph is what
    is true now. A full run reports every element and every link it found, so
    anything the graph holds that this run did not mention is no longer there.

    Nothing is deleted. A requirement whose code was removed is exactly the
    case a user needs told about, and it cannot be told if the row is gone.
    """
    if analysis.config_id is None or not source_kind or not target_kind:
        return

    config_id = analysis.config_id
    version_id = analysis.version_id

    stored = {
        (node.kind, node.identifier): node
        for node in db.execute(
            select(GraphNode).where(GraphNode.config_id == config_id)
        ).scalars()
    }

    # Before anything is compared: a file that moved is the same file, and its
    # elements should be recognised rather than mourned.
    if renames:
        moved = apply_renames(stored, renames)
        if moved:
            logger.info(f"Followed {moved} element(s) to their renamed files")

    nodes: dict[tuple[str, str], GraphNode] = {}
    for elements, kind in (
        (result.source_elements, source_kind),
        (result.target_elements, target_kind),
    ):
        for element in elements:
            key = (kind, element.identifier)
            node = stored.get(key)
            if node is None:
                node = GraphNode(
                    config_id=config_id,
                    kind=kind,
                    identifier=element.identifier,
                    first_seen_version_id=version_id,
                )
                db.add(node)
            node.content_hash = content_hash(element.content)
            node.level = element.level
            node.parent_identifier = element.parent_id
            node.last_seen_version_id = version_id
            node.is_active = True
            nodes[key] = node

    gone = [node for key, node in stored.items() if key not in nodes]
    for node in gone:
        node.is_active = False

    # Edges are keyed by node id, which a new node does not have until its
    # insert reaches the database.
    db.flush()

    stored_edges = {
        (edge.from_node_id, edge.to_node_id): edge
        for edge in db.execute(
            select(GraphEdge).where(GraphEdge.config_id == config_id)
        ).scalars()
    }

    found = set()
    for link in result.trace_links:
        source_node = nodes.get((source_kind, link.source_id))
        target_node = nodes.get((target_kind, link.target_id))
        if source_node is None or target_node is None:
            # A link naming something the run did not report as an element.
            continue

        pair = (source_node.node_id, target_node.node_id)
        found.add(pair)

        edge = stored_edges.get(pair)
        if edge is None:
            edge = GraphEdge(
                config_id=config_id,
                from_node_id=source_node.node_id,
                to_node_id=target_node.node_id,
                first_seen_version_id=version_id,
            )
            db.add(edge)
        edge.from_kind = source_kind
        edge.to_kind = target_kind
        edge.confidence = link.confidence
        edge.confidence_level = link.confidence_level
        edge.explanation = link.explanation
        edge.status = STATUS_ACTIVE
        edge.last_verified_version_id = version_id

    # An edge this run did not report is either broken - one of the elements it
    # joined is gone - or merely stale, where both still exist and the
    # classifier simply no longer links them. They read very differently to a
    # user, so they are not called the same thing.
    missing = {node.node_id for node in gone}
    for pair, edge in stored_edges.items():
        if pair in found:
            continue
        edge.status = (
            STATUS_BROKEN if missing.intersection(pair) else STATUS_STALE
        )

    record_element_links(db, config_id, getattr(result, "element_links", None))
    db.commit()


def version_numbers(db: Session, version_ids) -> dict[int, int]:
    """The number each version is known by, for a set of version ids.

    Looked up in one query for a whole page of analyses. An analysis stores the
    version it belongs to, but the user only ever sees the number - so without
    this a run cannot be matched to the state of the project it ran against.
    """
    wanted = {version_id for version_id in version_ids if version_id is not None}
    if not wanted:
        return {}

    return dict(db.execute(
        select(ProjectVersion.version_id, ProjectVersion.version_number)
        .where(ProjectVersion.version_id.in_(wanted))
    ).all())


def pinned_links(db: Session, config_id: int) -> dict[str, set[str]]:
    """Last run's links for this configuration, as source -> targets.

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
    stopped being relevant several syncs ago.

    An empty list and no list at all mean different things, so they are treated
    differently. `[]` is a run saying it linked nothing, which clears the store.
    `None` is a caller that has nothing to say - a result shape from before
    this existed - and leaves what is stored alone rather than discarding pins
    on the word of something that was never asked the question.
    """
    if links is None:
        return 0

    db.execute(delete(ElementLink).where(ElementLink.config_id == config_id))
    if not links:
        return 0
    # Deduplicated because dependency expansion and aggregation can both hand
    # back the same pair, and the table holds one row per pair.
    unique = {(source, target) for source, target in links}
    db.add_all([
        ElementLink(config_id=config_id, source_identifier=source, target_identifier=target)
        for source, target in unique
    ])

    logger.info(f"Kept {len(unique)} element-level link(s) for config {config_id}")
    return len(unique)


def graph_summary(db: Session, config_id: int) -> dict:
    """How this configuration's graph stands, in counts.

    Broken is the number worth acting on: those links lost an element, which
    is a requirement whose implementation is gone rather than a classifier
    that changed its mind.
    """
    nodes = dict(db.execute(
        select(GraphNode.is_active, func.count())
        .where(GraphNode.config_id == config_id)
        .group_by(GraphNode.is_active)
    ).all())
    edges = dict(db.execute(
        select(GraphEdge.status, func.count())
        .where(GraphEdge.config_id == config_id)
        .group_by(GraphEdge.status)
    ).all())

    return {
        "nodes_present": nodes.get(True, 0),
        "nodes_gone": nodes.get(False, 0),
        "links_active": edges.get(STATUS_ACTIVE, 0),
        "links_stale": edges.get(STATUS_STALE, 0),
        "links_broken": edges.get(STATUS_BROKEN, 0),
    }


def list_graph_edges(
    db: Session,
    config_id: int,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[tuple], int]:
    """One page of a configuration's links, with both ends named.

    Returns the rows and the total that matched, so a review queue can say how
    much is left without reading all of it.
    """
    source = aliased(GraphNode)
    target = aliased(GraphNode)

    def scoped(query):
        query = query.where(GraphEdge.config_id == config_id)
        return query.where(GraphEdge.status == status) if status else query

    total = db.execute(
        scoped(select(func.count()).select_from(GraphEdge))
    ).scalar_one()

    rows = db.execute(
        scoped(
            select(GraphEdge, source, target)
            .join(source, GraphEdge.from_node_id == source.node_id)
            .join(target, GraphEdge.to_node_id == target.node_id)
        )
        # Broken first, then the least confident: both ends of what a reviewer
        # wants to see before the links nobody needs to look at.
        .order_by(GraphEdge.status, GraphEdge.confidence)
        .limit(limit)
        .offset(offset)
    ).all()

    return list(rows), total


# ----- Analyses -----

def save_analysis(
    db: Session,
    project: Project,
    note: str | None,
    config,
    result,
    execution_duration: float | None,
    version_id: int | None = None,
    # The artifact kind on each side, which decides the configuration this run
    # is filed under. None when the run is saved without its files.
    source_kind: str | None = None,
    target_kind: str | None = None,
) -> Analysis:
    analysis = Analysis(
        project_id=project.project_id,
        config_id=get_or_create_config(
            db, project, config, source_kind, target_kind
        ).config_id,
        # Given by the caller, because only it knows whether these are new
        # artifacts or the stored ones a re-run reaches for.
        version_id=version_id,
        note=(note or "").strip() or None,
        source_preprocessor=config.source_preprocessor.value,
        target_preprocessor=config.target_preprocessor.value,
        source_output_level=config.source_output_level.value if config.source_output_level else None,
        target_output_level=config.target_output_level.value if config.target_output_level else None,
        top_k=config.n_results,
        dependency_expansion_depth=expansion_depth(
            target_kind, config.dependency_expansion_depth
        ),
        classifier_type=config.classifier.value,
        summarize_elements=config.summarize_elements,
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
    project.updated_at = utcnow()
    db.commit()
    db.refresh(analysis)
    return analysis


def latest_analysis(
    db: Session, project: Project, config_id: int | None = None
) -> Analysis | None:
    """The project's most recent run, or its most recent under one config.

    A sync reads this to learn what the project actually traces: the artifacts
    of a saved run record which kind sat on which side, which is not something
    a configuration says on its own.
    """
    query = select(Analysis).where(Analysis.project_id == project.project_id)
    if config_id is not None:
        query = query.where(Analysis.config_id == config_id)
    return db.execute(
        query.order_by(Analysis.created_at.desc(), Analysis.analysis_id.desc()).limit(1)
    ).scalar_one_or_none()


def list_configs(db: Session, project: Project) -> list[ProjectConfig]:
    """Every way this project has been read, oldest first."""
    return list(db.execute(
        select(ProjectConfig)
        .where(ProjectConfig.project_id == project.project_id)
        .order_by(ProjectConfig.config_id)
    ).scalars())


# ----- Pairs -----
#
# A project can hold several kinds of artifact, and a trace always runs between
# two of them. Those two kinds are a pair: the unit a sync brings up to date.
# Nothing stores a pair - it is whatever the project's configurations link.

def list_pairs(db: Session, project: Project) -> list[tuple[str, str]]:
    """Every (source kind, target kind) this project traces, oldest first."""
    pairs: list[tuple[str, str]] = []
    for config in list_configs(db, project):
        pair = (config.source_kind, config.target_kind)
        # A configuration saved without its files names no kinds, and so no pair.
        if all(pair) and pair not in pairs:
            pairs.append(pair)
    return pairs


def latest_pair_analysis(
    db: Session, project: Project, source_kind: str, target_kind: str
) -> Analysis | None:
    """The most recent run between these two kinds, under any configuration."""
    return db.execute(
        select(Analysis)
        .join(ProjectConfig, Analysis.config_id == ProjectConfig.config_id)
        .where(
            Analysis.project_id == project.project_id,
            ProjectConfig.source_kind == source_kind,
            ProjectConfig.target_kind == target_kind,
        )
        .order_by(Analysis.created_at.desc(), Analysis.analysis_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def latest_artifact(db: Session, source: ProjectSource) -> Artifact | None:
    """The files a source holds now: the last ones any run took in for it."""
    return db.execute(
        select(Artifact)
        .join(Analysis, Artifact.analysis_id == Analysis.analysis_id)
        .where(Artifact.source_id == source.source_id)
        .order_by(Analysis.created_at.desc(), Artifact.artifact_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def side_is_behind(db: Session, project: Project, artifact: Artifact) -> bool:
    """Whether the project now holds different files than this run read.

    A kind can be shared by two pairs. Syncing one of them moves the source on,
    and the other pair's last run is then about files the project no longer
    holds - which is said rather than silently re-run.
    """
    source = active_source_for_kind(db, project, artifact.artifact_type)
    if source is None:
        return False
    current = latest_artifact(db, source)
    # Connected but never fetched: there is nothing here to be behind.
    if current is None:
        return False
    return source_fingerprint(current.files) != source_fingerprint(artifact.files)


def _source_for_entry(
    db: Session, project: Project, entry: dict, artifact: Artifact
) -> ProjectSource:
    """The source an uploaded artifact belongs to.

    A sync names the source in the manifest, because the files it fetched came
    from a connected repository and must not be filed as a new upload beside
    it. A plain upload names nothing, and is matched the way it always was.
    """
    source_id = entry.get("source_id")
    if source_id is not None:
        named = db.get(ProjectSource, source_id)
        if named is not None and named.project_id == project.project_id:
            return named

    return get_or_create_source(
        db, project, kind=artifact.artifact_type, name=artifact.name
    )


def claim_artifacts(db: Session, analysis: Analysis, upload_id: str | None) -> int:
    """Take an upload's files into the blob store; returns artifacts attached."""
    # Zero is normal - no upload, aged out, or already claimed - so this never
    # raises and the analysis is kept either way.
    if not upload_id:
        return 0

    pending = artifact_store.upload_dir(upload_id)
    if pending is None or not pending.is_dir():
        return 0

    attached = 0
    claimed: list[ProjectSource] = []
    for entry in artifact_store.read_manifest(pending):
        files = artifact_store.collect_files(pending / entry.get("directory", ""))
        if not files:
            continue

        artifact = Artifact(
            analysis_id=analysis.analysis_id,
            name=entry.get("name") or "artifact",
            artifact_type=entry.get("artifact_type") or "unknown",
            role=entry.get("role") or "source",
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

        # What the project holds, as opposed to what this one run used. The
        # artifact rows are history and never move; this row is the current
        # state of the same artifact set, and is what a sync refreshes.
        source = _source_for_entry(db, analysis.project, entry, artifact)
        # A fetched source records the commit it came from, because that is
        # what the next check compares against. An upload has no such name, so
        # its contents are fingerprinted instead.
        source.last_sync_ref = entry.get("ref") or source_fingerprint(artifact.files)
        source.last_synced_at = utcnow()
        artifact.source_id = source.source_id
        claimed.append(source)

        db.add(artifact)
        attached += 1

    # A kind has one source at a time, so files uploaded under a new name
    # replace whatever supplied that kind before. Done before sealing, so the
    # version records the sources as this upload left them.
    together = {source.source_id for source in claimed}
    for source in claimed:
        _take_over_kind(db, analysis.project, source.kind, source, beside=together)

    # After the loop, so every source already carries the state this upload put
    # it in and the version is sealed against the finished picture.
    if attached and analysis.version_id is not None:
        seal_version(db, analysis.version_id, analysis.project)

    db.commit()
    # Only once every blob is referenced; dropping the upload before the commit
    # would risk losing the files.
    artifact_store.discard_upload(upload_id)
    return attached


def copy_artifacts(db: Session, source: Analysis, target: Analysis) -> int:
    """Point a new analysis at the same blobs - rows only, no bytes copied."""
    for artifact in source.artifacts:
        db.add(Artifact(
            analysis_id=target.analysis_id,
            name=artifact.name,
            artifact_type=artifact.artifact_type,
            role=artifact.role,
            # The same files, so the same source. A re-run changes the
            # configuration, never what the project holds.
            source_id=artifact.source_id,
            file_count=artifact.file_count,
            byte_size=artifact.byte_size,
            files=[
                ArtifactFile(
                    relative_path=f.relative_path,
                    sha256=f.sha256,
                    byte_size=f.byte_size,
                )
                for f in artifact.files
            ],
        ))

    db.commit()
    db.refresh(target)
    return len(target.artifacts)


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
        select(Artifact.analysis_id, ArtifactFile.sha256)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.artifact_id)
        .where(Artifact.analysis_id.in_(analysis_ids))
    ).all()

    # One filesystem check per distinct digest, not per row: a re-run shares
    # its original's blobs, so the same digest appears under several analyses.
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


def _child_count(model, id_column):
    # Two counts as joins would fan out against each other and inflate both.
    return (
        select(func.count(id_column))
        .where(model.analysis_id == Analysis.analysis_id)
        .correlate(Analysis)
        .scalar_subquery()
    )


def list_analyses(
    db: Session,
    user_id: int,
    project_id: int | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[tuple[Analysis, int, int, str]]:
    """Recent analyses as (analysis, link count, artifact count, project name)."""
    query = (
        select(
            Analysis,
            _child_count(TraceLink, TraceLink.trace_id),
            _child_count(Artifact, Artifact.artifact_id),
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
    db.delete(analysis)
    db.commit()
    collect_garbage(db)


def load_snapshot(analysis: Analysis) -> dict:
    try:
        return json.loads(analysis.snapshot_json) or {}
    except (TypeError, ValueError):
        return {}


# ----- Comparing two runs -----

# Differ on any of these and the two runs report at different granularities,
# so their identifiers describe different things and a diff is meaningless.
ALIGNING_SETTINGS = (
    "source_preprocessor",
    "target_preprocessor",
    "source_output_level",
    "target_output_level",
)

COMPARED_SETTINGS = ALIGNING_SETTINGS + (
    "classifier_type",
    "top_k",
    "dependency_expansion_depth",
    # Not aligning: summaries change which candidates retrieval returns, but
    # not what an element is called, so the two runs' links still line up.
    "summarize_elements",
)

LINK_FIELDS = ("similarity_score", "confidence_level", "explanation")


def _link_index(db: Session, analysis_id: int) -> dict[tuple[str, str], dict]:
    # Content is left out: a run can hold thousands of links and the diff only
    # needs identifiers. The text of the few that changed is fetched after.
    rows = db.execute(
        select(
            TraceLink.source_id,
            TraceLink.target_id,
            TraceLink.similarity_score,
            TraceLink.confidence_level,
            TraceLink.explanation,
        ).where(TraceLink.analysis_id == analysis_id)
    ).all()

    return {
        (row.source_id, row.target_id): {
            "similarity_score": row.similarity_score,
            "confidence_level": row.confidence_level,
            "explanation": row.explanation,
        }
        for row in rows
    }


def _content_index(
    db: Session, analysis_id: int, keys: set[tuple[str, str]]
) -> dict[tuple[str, str], dict]:
    if not keys:
        return {}

    rows = db.execute(
        select(
            TraceLink.source_id,
            TraceLink.target_id,
            TraceLink.source_content,
            TraceLink.target_content,
        ).where(
            TraceLink.analysis_id == analysis_id,
            tuple_(TraceLink.source_id, TraceLink.target_id).in_(list(keys)),
        )
    ).all()

    return {
        (row.source_id, row.target_id): {
            "source_content": row.source_content,
            "target_content": row.target_content,
        }
        for row in rows
    }


def config_differences(base: Analysis, head: Analysis) -> list[dict]:
    return [
        {
            "setting": setting,
            "base": None if getattr(base, setting) is None else str(getattr(base, setting)),
            "head": None if getattr(head, setting) is None else str(getattr(head, setting)),
        }
        for setting in COMPARED_SETTINGS
        if getattr(base, setting) != getattr(head, setting)
    ]


def unimplemented_ids(analysis: Analysis) -> set[str]:
    return {
        item.get("identifier")
        for item in load_snapshot(analysis).get("unimplemented", [])
        if item.get("identifier")
    }


def compare_analyses(db: Session, base: Analysis, head: Analysis) -> dict:
    """Diff two runs. A link is identified by the pair it connects."""
    base_links = _link_index(db, base.analysis_id)
    head_links = _link_index(db, head.analysis_id)

    added_keys = set(head_links) - set(base_links)
    removed_keys = set(base_links) - set(head_links)

    modified = []
    unchanged = 0
    for key in set(base_links) & set(head_links):
        before, after = base_links[key], head_links[key]
        changed = [field for field in LINK_FIELDS if before[field] != after[field]]
        if changed:
            modified.append({"key": key, "changed_fields": changed,
                             "base": before, "head": after})
        else:
            unchanged += 1

    modified_keys = {entry["key"] for entry in modified}
    head_content = _content_index(db, head.analysis_id, added_keys | modified_keys)
    base_content = _content_index(db, base.analysis_id, removed_keys)

    base_unimplemented = unimplemented_ids(base)
    head_unimplemented = unimplemented_ids(head)

    return {
        "config_differences": config_differences(base, head),
        "comparable": all(
            getattr(base, setting) == getattr(head, setting)
            for setting in ALIGNING_SETTINGS
        ),
        "summary": {
            "base_total": len(base_links),
            "head_total": len(head_links),
            "added": len(added_keys),
            "removed": len(removed_keys),
            "modified": len(modified),
            "unchanged": unchanged,
        },
        "added": [
            {"source_id": s, "target_id": t, **head_links[(s, t)],
             **head_content.get((s, t), {})}
            for s, t in sorted(added_keys)
        ],
        "removed": [
            {"source_id": s, "target_id": t, **base_links[(s, t)],
             **base_content.get((s, t), {})}
            for s, t in sorted(removed_keys)
        ],
        "modified": [
            {
                "source_id": entry["key"][0],
                "target_id": entry["key"][1],
                "changed_fields": entry["changed_fields"],
                **head_content.get(entry["key"], {}),
                **{f"base_{k}": v for k, v in entry["base"].items()},
                **{f"head_{k}": v for k, v in entry["head"].items()},
            }
            for entry in sorted(modified, key=lambda e: e["key"])
        ],
        "newly_implemented": sorted(base_unimplemented - head_unimplemented),
        "newly_unimplemented": sorted(head_unimplemented - base_unimplemented),
    }
