"""Tables: User -> Project -> Analysis -> TraceLink, cascading on delete.

A project also carries the state sync works against, which is a second shape
laid beside the first rather than replacing it:

    Project -> ProjectSource    what can be refreshed, and where it came from
    Project -> ProjectVersion   one per sync; what the artifacts were then
    Project -> ProjectConfig    one per lens; how a run reads those artifacts
    ProjectConfig -> GraphNode -> GraphEdge   the living graph for that lens

Analyses stay immutable snapshots of one run. The graph is the current answer
and is patched in place. Neither is derived from the other.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    ARRAY, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # What this person authorised TraceRAG to reach on GitHub, encrypted.
    # Held on the user rather than on each repository they connect: a token
    # belongs to a person, and putting it here means connecting a second
    # repository asks for nothing, and revoking covers all of them at once.
    github_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Their GitHub username, purely so the app can say whose account it is.
    github_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    projects: Mapped[list["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Project(Base):
    """A named workspace holding one codebase's analyses over time."""

    __tablename__ = "projects"

    project_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    # Bumped whenever an analysis is saved, so "last activity" does not require
    # reading every child row.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="projects")
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Analysis.created_at.desc()",
    )
    sources: Mapped[list["ProjectSource"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    versions: Mapped[list["ProjectVersion"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectVersion.version_number.desc()",
    )
    configs: Mapped[list["ProjectConfig"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Analysis(Base):
    """One saved run: the settings it used and the links it recovered.
    """

    __tablename__ = "analyses"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Which artifact state this ran against, and which lens it read them
    # through. Two runs are only comparable when both match: a different
    # version is the change being measured, a different config means the
    # identifiers on each side describe different things.
    #
    # Both are nullable and cleared rather than cascaded on delete: runs saved
    # before versioning existed have neither, and removing a config must not
    # take the history of what it once found with it. The settings columns
    # below still record what the run actually used either way.
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    config_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # What the user says is different about this run - "reported at class
    # level", "summaries off". Runs are identified by when they ran; this says
    # why this one exists, which is what a comparison is read against.
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source_preprocessor: Mapped[str] = mapped_column(String(50), nullable=False)
    target_preprocessor: Mapped[str] = mapped_column(String(50), nullable=False)
    source_output_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_output_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_expansion_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Whether elements were described by the model before being embedded. It
    # changes which candidates retrieval returns, so a re-run has to repeat it
    # and a comparison has to be able to name it as the reason links moved.
    summarize_elements: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Everything except the links: element inventory, unimplemented sources,
    # summary counts. A snapshot of how the artifacts were chunked, not
    # entities anything refers to, so one JSON column rather than more tables.
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    project: Mapped["Project"] = relationship(back_populates="analyses")
    trace_links: Mapped[list["TraceLink"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class Artifact(Base):
    """The files one saved analysis was run against."""
    # Hung off the analysis, not the project as the SRS had it: a project is
    # re-analysed as its artifacts change, and one current set per project
    # would rewrite history each time.

    __tablename__ = "artifacts"

    artifact_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.analysis_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Which of the project's sources these files came from. The rows below say
    # what was analysed; this says what it was analysed *as*, so a run can be
    # read back as "the requirements source, at the state it was in then".
    # Nullable: uploads saved before sources existed have no source to name.
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_sources.source_id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # "requirements" or "code" - the key from the capabilities registry.
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Which side of the trace it was on: "source" or "target".
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # What the user uploaded, not what it cost to keep: files shared with
    # another analysis are stored once but still counted here, because this is
    # the size of the artifact that was analysed.
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    analysis: Mapped["Analysis"] = relationship(back_populates="artifacts")
    files: Mapped[list["ArtifactFile"]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class ArtifactFile(Base):
    """One file inside a stored artifact: its name here, its bytes elsewhere.

    The blob store is keyed purely by content, so names and ownership live
    here. Two runs over an unchanged codebase differ only in these rows.
    """

    __tablename__ = "artifact_files"

    file_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Path within the artifact, so an upload keeps its folder structure.
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # SHA-256 of the contents; indexed because garbage collection asks which
    # digests are still spoken for.
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    artifact: Mapped["Artifact"] = relationship(back_populates="files")


class EmbeddingCacheEntry(Base):
    """One text's embedding, kept so it is never paid for twice."""
    # Not tied to a project or user: the key is the hash of the text, and the
    # same text under the same model always embeds to the same vector, so two
    # projects sharing a file share the result. Purely derived data.

    __tablename__ = "embedding_cache"

    # Identifies the model, tokenizer and token limit the vector was produced
    # with, so changing any of them starts a fresh cache rather than returning
    # vectors from a different embedding space.
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # A native float array rather than JSON: no parsing on read, and roughly
    # half the bytes for a 768-dimension vector.
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SummaryCacheEntry(Base):
    """One element's summary, kept so the model is never asked for it twice."""
    # Keyed like the embedding cache and for the same reason: a summary depends
    # only on the text it describes, so unchanged code is summarised once and
    # shared by every project that contains it. Purely derived data.

    __tablename__ = "summary_cache"

    # The model that wrote it - a different one writes different summaries, and
    # mixing them would put two descriptions of the same code in one index.
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TraceLink(Base):
    """One recovered requirement-to-code link."""
    # Content is denormalised on purpose: a saved analysis must keep showing
    # what it found even after the artifacts change underneath it.

    __tablename__ = "trace_links"

    trace_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.analysis_id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str] = mapped_column(String(500), nullable=False)
    target_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Derived from the score, but stored rather than recomputed so a saved
    # analysis is not re-bucketed if the thresholds are ever tuned.
    confidence_level: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    analysis: Mapped["Analysis"] = relationship(back_populates="trace_links")


class ProjectSource(Base):
    """One artifact set a project can refresh, and where it is refreshed from."""
    # Artifacts hang off an analysis, which records what one run used. Nothing
    # said what a project *has* - so syncing had nothing to offer the user and
    # nothing to re-fetch. This is that missing list.

    __tablename__ = "project_sources"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # The key from the capabilities registry: "requirements", "code", and
    # whichever kinds come later. A plain string, so adding one is a registry
    # entry rather than a migration.
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # "upload" or "github". Decides whether a sync re-fetches this source on
    # its own or has to ask the user to supply it again.
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    # Where a connected source lives - "owner/repo" for GitHub. Null for
    # uploads, which have no address to return to.
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Encrypted, and only for a source whose provider needs one of its own - a
    # private repository the server's own token cannot see. Never returned to
    # a client: once given, a token can be replaced but not read back.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What this source was when it was last taken in: a commit sha for a
    # connected repository, or a fingerprint of the file contents for an
    # upload. Either way, comparing it is how a sync decides nothing moved.
    last_sync_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Disconnected sources are hidden rather than removed. Every version this
    # source appeared in recorded what it was at the time, and deleting the row
    # would take that record with it - leaving two versions looking identical
    # when the whole point of keeping them was that they were not.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="sources")


class ProjectVersion(Base):
    """The state of a project's artifacts at one point. A sync makes the next one."""
    # Change is only ever measured between two of these. Analyses are ordered
    # by when they ran, which says nothing about whether the artifacts under
    # them moved in between - so re-running a config twice on an unchanged
    # codebase must not read as a change, and this is what tells them apart.

    __tablename__ = "project_versions"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Counts from 1 within the project, so a user can say "version 3" without
    # knowing the row id.
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="versions")
    # Left to the database on delete: these rows hang off a source as well as a
    # version, so deleting a project reaches them by two routes and whichever
    # arrives second would find its work already done.
    sources: Mapped[list["VersionSource"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (UniqueConstraint("project_id", "version_number"),)


class VersionSource(Base):
    """What one source was at one version."""
    # A version is not a timestamp, it is the set of refs its sources were at.
    # Kept apart from ProjectSource because that row moves forward and this one
    # must not: it is how a past version stays readable.

    __tablename__ = "version_sources"

    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("project_sources.source_id", ondelete="CASCADE"), primary_key=True
    )
    # A commit sha, or a fingerprint of the contents that were uploaded.
    ref: Mapped[str] = mapped_column(String(255), nullable=False)

    version: Mapped["ProjectVersion"] = relationship(back_populates="sources")
    # Read-only from this side: a source does not need to carry every version
    # that ever recorded it, but a version does need to say what each one was.
    source: Mapped["ProjectSource"] = relationship()


class ProjectConfig(Base):
    """One lens on a project: how its artifacts are split, embedded and judged."""
    # Two runs are comparable only if they read the artifacts the same way. A
    # file-level run and a sentence-level run produce different elements from
    # the same file, so diffing them would report every element as new. That
    # constraint is what this row exists to name: a config is the unit a graph,
    # an element set and a comparison all belong to.

    __tablename__ = "project_configs"

    config_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Hash of the settings below, so the same configuration is recognised as
    # the same lens across versions. Sixteen hex characters rather than the
    # full digest: it also names this config's vector collections, and those
    # have a length limit.
    config_key: Mapped[str] = mapped_column(String(16), nullable=False)
    # Which config the dashboard shows when the user has not picked one.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Which two artifact kinds this lens links - keys from the capabilities
    # registry. Null only for a run saved without its files, where nothing
    # recorded what it was pointed at.
    source_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_preprocessor: Mapped[str] = mapped_column(String(50), nullable=False)
    target_preprocessor: Mapped[str] = mapped_column(String(50), nullable=False)
    source_output_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_output_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_expansion_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
    summarize_elements: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="configs")
    nodes: Mapped[list["GraphNode"]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("project_id", "config_key"),)


class GraphNode(Base):
    """One element as it currently stands, under one lens."""
    # Doubles as the state a sync diffs against: content_hash is what says an
    # element is unchanged, so an unchanged element is never re-embedded and
    # never re-classified. Identity is the identifier, which survives an edit
    # but not a rename - a renamed element arrives as a delete and an add, and
    # matching the two back together is the differ's job, not this table's.

    __tablename__ = "graph_nodes"

    node_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The project is reachable through the config, so it is not repeated here.
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    identifier: Mapped[str] = mapped_column(String(500), nullable=False)

    # Of the element's content, after normalising away what carries no meaning
    # - whitespace, and the numbering an element is written under. Otherwise
    # reformatting a file or renumbering a document reads as a full rewrite.
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_identifier: Mapped[str | None] = mapped_column(String(500), nullable=True)

    first_seen_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="SET NULL"), nullable=True
    )
    last_seen_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="SET NULL"), nullable=True
    )
    # Deleted elements are deactivated rather than removed: the links that
    # pointed at them are the reason a user is told something broke.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    config: Mapped["ProjectConfig"] = relationship(back_populates="nodes")

    __table_args__ = (UniqueConstraint("config_id", "kind", "identifier"),)


class GraphEdge(Base):
    """One recovered link, as it currently stands."""
    # The mutable twin of TraceLink: that one records what a run found and is
    # never touched again, this one is patched each sync and is what chains,
    # coverage and impact are read from.

    __tablename__ = "graph_edges"

    edge_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph_nodes.node_id", ondelete="CASCADE"), index=True, nullable=False
    )
    to_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph_nodes.node_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Repeated from the nodes on purpose. Walking a chain asks "which edges
    # leave this node for a test?" at every step, and answering it from the
    # edge alone avoids joining both ends of every candidate.
    from_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    to_kind: Mapped[str] = mapped_column(String(50), nullable=False)

    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "active", "stale" when a re-run stopped finding it, or "broken" when an
    # element it depended on is gone.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    first_seen_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="SET NULL"), nullable=True
    )
    last_verified_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("config_id", "from_node_id", "to_node_id"),)


class ElementLink(Base):
    """One link as the classifier actually made it, before it was rolled up.

    A graph edge is reported at the configuration's output level, so a
    file-level edge does not say which method earned it. That is fine to look
    at and useless to act on: the classifier takes elements, and a file is not
    an element when the processing level is methods.

    So the pairs are kept here at the level they were judged. The next run puts
    them back in front of the classifier alongside whatever retrieval turns up,
    which is what stops a link disappearing because a growing corpus pushed it
    out of the top-k rather than because anything decided against it.

    Living state, like the graph: replaced wholesale on every run. Only the
    last run's pairs are worth offering again - keeping older ones would mean
    re-proposing pairs that stopped being relevant several syncs ago.
    """

    __tablename__ = "element_links"

    element_link_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Identifiers rather than node ids: these are processing-level elements,
    # which have no graph node of their own whenever the output level is
    # coarser than the processing level.
    source_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    target_identifier: Mapped[str] = mapped_column(String(500), nullable=False)

    __table_args__ = (UniqueConstraint("config_id", "source_identifier", "target_identifier"),)


class Job(Base):
    """Work that outlives the request that asked for it.

    A sync fetches, re-runs every configuration and rewrites a graph, which
    takes minutes. Done inside the request, the browser gives up long before
    the server does and the answer is lost even though the work succeeded. So
    the request only files this row and returns; the client watches it instead.
    """

    __tablename__ = "jobs"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Who may watch it. Null for a run started without an account, which the
    # analysis flow allows - those are watched by their token instead.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True, nullable=True
    )
    # Handed to whoever asked for the work and to nobody else. Job ids run in
    # sequence, so holding one is no proof of having started it.
    token: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # What it is working on, and what makes two jobs conflict: one project is
    # only ever synced by one job at a time.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # What it is doing now, in words a user can read while they wait.
    stage: Mapped[str | None] = mapped_column(String(200), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The answer the request would have returned had it waited, kept so the
    # client gets exactly the same thing whichever way it asked.
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClassificationCacheEntry(Base):
    """One verdict on one pair of elements, kept so it is never asked for twice."""
    # The third cache, and the one that decides whether syncing is affordable:
    # embeddings and summaries were already cached, but the classifier - the
    # only step that costs a model call per pair - was not, so re-running an
    # unchanged project paid full price every time.
    #
    # Keyed like the others, by content rather than by identity. The prompt
    # names neither element, only their text, so a method that was renamed and
    # not otherwise touched is the same question and keeps the same answer.

    __tablename__ = "classification_cache"

    # The classifier, the model behind it, and the prompt it asked with. A
    # change to any of them is a different question, not a cheaper answer.
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True)
    pair_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The whole of what the model decides. The score reported on a link is the
    # retrieval similarity, which belongs to the run rather than to the pair,
    # so it is deliberately not stored here.
    linked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
