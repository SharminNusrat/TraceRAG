"""Tables: User -> Project -> analyses, cascading on delete.

An analysis is one traceability relation between two sides, read with its own
settings. It owns everything that changes over time, so two analyses never
share a version, a pin or a side - not even when they read the same files:

    Project -> ProjectConfig                  one analysis: its kinds and settings
    ProjectConfig -> ProjectSource            its two sides, and where each comes from
    ProjectConfig -> ProjectVersion           one per state of its files
    ProjectVersion -> Artifact -> ArtifactFile   both sides' files at that state
    ProjectVersion -> Analysis -> TraceLink   the runs made against that state
    ProjectConfig -> ElementLink              the pairs the next run re-offers

What changed between two versions is not kept as a living graph: it is worked
out from the two versions' runs when it is asked for (core.projects.report).

The names are older than this shape: a ProjectConfig row is the analysis, and
an Analysis row is one run of it. A project is only the folder they sit in.
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
    """A named folder of analyses. It owns nothing they share."""

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
    configs: Mapped[list["ProjectConfig"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Analysis(Base):
    """One run of an analysis: the links it recovered from one version's files."""
    # The settings are not repeated here. They belong to the analysis, and a
    # run cannot use any others - different settings are a different analysis.

    __tablename__ = "analyses"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Which analysis this is a run of, and which state of its files it read.
    # Several runs can share a version: a re-run reads the same files again.
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # What the user says is different about this run. Runs are identified by
    # when they ran; this says why this one exists.
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    execution_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Everything except the links: element inventory, unimplemented sources,
    # summary counts. A snapshot of how the artifacts were chunked, not
    # entities anything refers to, so one JSON column rather than more tables.
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    project: Mapped["Project"] = relationship(back_populates="analyses")
    config: Mapped["ProjectConfig"] = relationship()
    version: Mapped["ProjectVersion"] = relationship(back_populates="analyses")
    trace_links: Mapped[list["TraceLink"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )

    @property
    def artifacts(self) -> list["Artifact"]:
        """The files this run read: its version's, which every run of it shares."""
        return self.version.artifacts


class Artifact(Base):
    """One side's complete file set at one version of an analysis."""
    # Hung off the version, not the run: every run of a version reads the very
    # same files, so a re-run adds a run and not another copy of the file list.

    __tablename__ = "artifacts"

    artifact_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # "requirements" or "code" - the key from the capabilities registry.
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Which side of the trace it is: "source" or "target".
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # Where these files came from - "upload" or "github" - and what they were:
    # the commit they were fetched at, or a fingerprint of their contents for
    # an upload. What the next update compares against.
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # What the user uploaded, not what it cost to keep: files shared with
    # another analysis are stored once but still counted here, because this is
    # the size of the artifact that was analysed.
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    version: Mapped["ProjectVersion"] = relationship(back_populates="artifacts")
    files: Mapped[list["ArtifactFile"]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class ArtifactFile(Base):
    """One file inside a stored artifact: its name here, its bytes elsewhere.

    The blob store is keyed purely by content, so names and ownership live
    here. Two versions that share a file list it twice and store it once.
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


class ProjectConfig(Base):
    """One analysis: a traceability relation between two sides, and its settings."""
    # Made by every New Analysis and never looked up by its settings: two
    # analyses with identical settings are still two analyses, each with its
    # own sides, versions and pins. The settings live here and nowhere else,
    # because a run can only ever use its analysis's settings.

    __tablename__ = "project_configs"

    config_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Which two artifact kinds this analysis links - keys from the capabilities
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
    # Whether elements were described by the model before being embedded. It
    # changes which candidates retrieval returns, so every run of the analysis
    # repeats it.
    summarize_elements: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="configs")
    sources: Mapped[list["ProjectSource"]] = relationship(
        back_populates="config", cascade="all, delete-orphan", order_by="ProjectSource.role"
    )
    versions: Mapped[list["ProjectVersion"]] = relationship(
        back_populates="config",
        cascade="all, delete-orphan",
        order_by="ProjectVersion.version_number.desc()",
    )


class ProjectSource(Base):
    """One side of an analysis, and where its files come from."""
    # A side belongs to exactly one analysis. The same requirements traced to
    # code and to a model are two sides of two analyses, updated separately:
    # sharing them is what made one analysis fall out of date behind another.

    __tablename__ = "project_sources"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "source" or "target": which end of the trace this side is.
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # The key from the capabilities registry: "requirements", "code", and
    # whichever kinds come later.
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # "upload" or "github". Decides whether an update fetches this side on its
    # own or has to ask the user to supply the files again.
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    # Where a connected side lives - "owner/repo" for GitHub. Null for
    # uploads, which have no address to return to.
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Encrypted, and only for a repository the user's own connection cannot
    # see. Never returned to a client: once given, a token can be replaced but
    # not read back.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The newest commit an update fetched from the repository, whether or not
    # it made a version. A commit whose files change nothing the analysis
    # reads makes no version, and without this the side would report that
    # commit as new every time it was asked. What the side holds is not kept
    # here: it is the files of the analysis's newest version.
    checked_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    config: Mapped["ProjectConfig"] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("config_id", "role"),)


class ProjectVersion(Base):
    """One state of an analysis's files. Changing a side makes the next one."""
    # Change is only ever measured between two of these. Runs are ordered by
    # when they ran, which says nothing about whether the files under them
    # moved in between - so re-running on unchanged files must not read as a
    # change, and this is what tells the two apart.

    __tablename__ = "project_versions"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Counts from 1 within the analysis, so a user can say "version 3" without
    # knowing the row id.
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # What changed since the version before, for the side that was updated:
    # the files and elements added, removed, modified and moved, and the id
    # map from the old identifiers to the new. Stored rather than worked out
    # again, because it can never change once the version exists - and the
    # old files it was worked out from may since have been collected. Null
    # for version 1, which has nothing before it.
    changes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    config: Mapped["ProjectConfig"] = relationship(back_populates="versions")
    # Both sides' complete file sets: what this version is.
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="Artifact.role"
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("config_id", "version_number"),)


class ElementLink(Base):
    """One link as the classifier actually made it, before it was rolled up.

    A link is reported at the analysis's output level, so a file-level link
    does not say which method earned it. That is fine to look at and useless
    to act on: the classifier takes elements, and a file is not an element when
    the processing level is methods.

    So the pairs are kept here at the level they were judged. The next run puts
    them back in front of the classifier alongside whatever retrieval turns up,
    which is what stops a link disappearing because a growing corpus pushed it
    out of the top-k rather than because anything decided against it. An
    update translates them through its id map first, so a pin follows its
    element when the element's identifier moves.

    Replaced wholesale on every run. Only the last run's pairs are worth
    offering again - keeping older ones would mean re-proposing pairs that
    stopped being relevant several updates ago.
    """

    __tablename__ = "element_links"

    element_link_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Processing-level identifiers, which are finer than the level links are
    # reported at whenever the output level is coarser.
    source_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    target_identifier: Mapped[str] = mapped_column(String(500), nullable=False)

    __table_args__ = (UniqueConstraint("config_id", "source_identifier", "target_identifier"),)


class Job(Base):
    """Work that outlives the request that asked for it.

    An update fetches, compares and re-runs an analysis, which takes minutes. Done inside the request, the browser gives up long before the
    server does and the answer is lost even though the work succeeded. So the
    request only files this row and returns; the client watches it instead.
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
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=True
    )
    # The analysis it is updating, and what makes two jobs conflict. Analyses
    # share nothing, so two different ones can be updated at the same time.
    config_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=True
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
    # The third cache, and the one that decides whether updating is affordable:
    # embeddings and summaries were already cached, but the classifier - the
    # only step that costs a model call per pair - was not, so re-running an
    # unchanged analysis paid full price every time.
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
