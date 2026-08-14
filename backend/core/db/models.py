"""Tables: User -> Project -> Analysis -> TraceLink, cascading on delete."""

from datetime import datetime, timezone
from sqlalchemy import ARRAY, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored lower-cased by the auth service, so the unique index also makes
    # the address unique case-insensitively.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
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


class Analysis(Base):
    """One saved run: the settings it used and the links it recovered.

    Deviates from the SRS table: each side has its own preprocessor and output
    level, and dependency expansion is a depth rather than a boolean.
    """

    __tablename__ = "analyses"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    source_preprocessor: Mapped[str] = mapped_column(String(50), nullable=False)
    target_preprocessor: Mapped[str] = mapped_column(String(50), nullable=False)
    source_output_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_output_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_expansion_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
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
