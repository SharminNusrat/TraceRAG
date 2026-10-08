"""Reading an analysis request's artifacts and writing each side to disk."""

import json
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from api.capabilities import ARTIFACT_KINDS_BY_KEY
from api.workspace import sanitize_project_id
from core.projects.uploads import (
    UploadBudget, UploadError, UploadTooLarge, extract_archive, safe_relative_path, save_upload,
)


async def upload_error_response(request: Request, error: UploadError) -> JSONResponse:
    """Answer an upload refusal the way an HTTPException would have been answered."""
    status_code = 413 if isinstance(error, UploadTooLarge) else 400
    return JSONResponse(status_code=status_code, content={"detail": str(error)})


def parse_json_field(raw: str, field: str, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail=f"'{field}' is not valid JSON: {error.msg}")


def parse_id_list(raw: str, field: str) -> list[str]:
    value = parse_json_field(raw, field, None)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise HTTPException(status_code=400, detail=f"'{field}' must be a JSON array of artifact ids.")


def resolve_side(artifacts: list[dict], artifact_ids: list[str], role: str) -> list[dict]:
    """Validate the artifacts chosen for one side of the trace."""
    if not artifact_ids:
        raise HTTPException(status_code=400, detail=f"Select at least one {role} artifact.")

    chosen = []
    for artifact_id in artifact_ids:
        artifact = next((a for a in artifacts if a.get("id") == artifact_id), None)
        if artifact is None:
            raise HTTPException(
                status_code=400,
                detail=f"No uploaded artifact matches '{artifact_id}'.",
            )

        kind = ARTIFACT_KINDS_BY_KEY.get(artifact.get("kind"))
        if kind is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown artifact kind '{artifact.get('kind')}'.",
            )
        if role not in kind.roles:
            raise HTTPException(
                status_code=400,
                detail=f"'{kind.label}' artifacts cannot be used as the {role} of a trace.",
            )
        chosen.append(artifact)

    kinds = {artifact.get("kind") for artifact in chosen}
    if len(kinds) > 1:
        labels = sorted(ARTIFACT_KINDS_BY_KEY[k].label for k in kinds)
        raise HTTPException(
            status_code=400,
            detail=f"The {role} side mixes {' and '.join(labels)} artifacts. Group one kind at a time.",
        )

    return chosen


def materialise_side(
    artifacts: list[dict],
    uploads: list[UploadFile],
    paths: list[str],
    side_dir: Path,
    budget: UploadBudget,
) -> Path:
    """Write every artifact on one side into a single directory."""
    side_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()

    for artifact in artifacts:
        kind = ARTIFACT_KINDS_BY_KEY[artifact["kind"]]
        artifact_name = artifact.get("name") or artifact.get("id") or kind.key
        namespace = Path(sanitize_project_id(artifact_name))

        text = (artifact.get("text") or "").strip()
        if text:
            if not kind.accepts_text:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{kind.label}' artifacts cannot be provided as pasted text.",
                )
            destination = side_dir / f"{sanitize_project_id(artifact_name)}.txt"
            destination.write_text(text, encoding="utf-8")
            written.add(destination.name)
            continue

        indexes = artifact.get("file_indexes") or []
        if not indexes:
            raise HTTPException(
                status_code=400,
                detail=f"Artifact '{artifact_name}' has no files.",
            )

        allowed = set(kind.extensions)
        for index in indexes:
            if not isinstance(index, int) or not 0 <= index < len(uploads):
                raise HTTPException(status_code=400, detail=f"File index {index} is out of range.")

            upload = uploads[index]
            raw_path = paths[index] if index < len(paths) else (upload.filename or "")
            relative = safe_relative_path(raw_path or upload.filename or "file")
            suffix = relative.suffix.lower()

            if suffix == ".zip":
                if not kind.accepts_archive:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{kind.label}' artifacts do not accept .zip archives.",
                    )
                staged = side_dir / f"__archive_{index}.zip"
                save_upload(upload, staged, budget)
                # Several archives on one side each get their own subtree.
                destination = side_dir if len(artifacts) == 1 else side_dir / namespace
                extract_archive(staged, destination, budget)
                continue

            if suffix not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{relative.name}' is not a supported {kind.label.lower()} file. "
                        f"Accepted: {', '.join(sorted(allowed))}"
                        + (" or a .zip archive." if kind.accepts_archive else ".")
                    ),
                )

            if str(relative) in written:
                relative = namespace / relative
            written.add(str(relative))
            save_upload(upload, side_dir / relative, budget)

    return side_dir


def side_manifest(artifacts: list[dict], role: str) -> dict:
    """Describe one side of the trace for later storage."""
    names = [artifact.get("name") or artifact.get("id") or "artifact" for artifact in artifacts]
    return {
        "role": role,
        "artifact_type": artifacts[0]["kind"],
        "name": ", ".join(names)[:255],
        "directory": role,
    }
