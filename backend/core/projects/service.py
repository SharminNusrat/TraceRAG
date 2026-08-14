"""Project and saved-analysis persistence, always scoped to an owner."""

import json

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from core.db.models import Analysis, Artifact, ArtifactFile, Project, TraceLink, utcnow
from core.projects import artifact_store

DEFAULT_HISTORY_LIMIT = 50


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


# ----- Analyses -----

def save_analysis(
    db: Session,
    project: Project,
    version_name: str | None,
    config,
    result,
    execution_duration: float | None,
) -> Analysis:
    analysis = Analysis(
        project_id=project.project_id,
        version_name=(version_name or "").strip() or None,
        source_preprocessor=config.source_preprocessor.value,
        target_preprocessor=config.target_preprocessor.value,
        source_output_level=config.source_output_level.value if config.source_output_level else None,
        target_output_level=config.target_output_level.value if config.target_output_level else None,
        top_k=config.n_results,
        dependency_expansion_depth=config.dependency_expansion_depth,
        classifier_type=config.classifier.value,
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
        db.add(artifact)
        attached += 1

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
    """Drop blobs no artifact references any more."""
    return artifact_store.collect_garbage(
        set(db.scalars(select(ArtifactFile.sha256).distinct()))
    )


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
