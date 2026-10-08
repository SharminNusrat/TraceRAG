"""Tables: User -> Project -> analyses, cascading on delete.

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

    __tablename__ = "analyses"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.version_id", ondelete="CASCADE"), index=True, nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    execution_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

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
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    version: Mapped["ProjectVersion"] = relationship(back_populates="artifacts")
    files: Mapped[list["ArtifactFile"]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class ArtifactFile(Base):
    """One file inside a stored artifact: its name here, its bytes elsewhere."""

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
    # same text under the same model always embeds to the same vector.

    __tablename__ = "embedding_cache"

    # Identifies the model, tokenizer and token limit the vector was produced
    # with, so changing any of them starts a fresh cache rather than returning
    # vectors from a different embedding space.
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SummaryCacheEntry(Base):
    """One element's summary, kept so the model is never asked for it twice."""
    # Keyed like the embedding cache and for the same reason

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

    __tablename__ = "project_configs"

    config_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
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

    __tablename__ = "project_sources"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "source" or "target": which end of the trace this side is.
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    # Where a connected side lives - "owner/repo" for GitHub. Null for
    # uploads, which have no address to return to.
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Encrypted, and only for a repository the user's own connection cannot
    # see.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The newest commit an update fetched from the repository, whether or not
    # it made a version.
    checked_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    config: Mapped["ProjectConfig"] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("config_id", "role"),)


class ProjectVersion(Base):
    """One state of an analysis's files. Changing a side makes the next one."""
    # Change is only ever measured between two of these.

    __tablename__ = "project_versions"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("project_configs.config_id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
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

    # Acts as a persistent memory layer that re-injects previously validated micro-level links into the next classifier run to prevent them from being pushed out by new data.
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

    # Acts as an asynchronous background task tracker that lets long-running AI tasks execute safely without causing HTTP browser timeouts.
    """

    __tablename__ = "jobs"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True, nullable=True
    )
    token: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=True
    )
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
