"""What changed between two file sets of one side, read from disk or from the blob store.

The comparison itself is pure (core.projects.diff). This lays the files out
for it: the old ones are always restored from their blobs, the new ones come
from a staged upload or from the blobs of a later version.
"""

import json
import shutil
import tempfile
from difflib import unified_diff
from pathlib import Path

from api.capabilities import ARTIFACT_KINDS_BY_KEY, ROLE_SOURCE, ROLE_TARGET
from api.pipeline_factory import read_items
from api.schemas import PreprocessorType
from core.db.models import ProjectConfig, ProjectVersion
from core.projects import artifact_store
from core.projects.diff import SideChanges, chain_renames, diff_elements, diff_files, element_hash


# Longer diffs are cut here: past this the file is better downloaded than read.
MAX_DIFF_LINES = 200

# Kept as text in name only: their bytes are a document format, not lines.
NOT_TEXT = (".pdf", ".docx")


class FilesMissing(RuntimeError):
    """A stored file's bytes are no longer on disk, so the comparison would be incomplete."""


def preprocessor_of(config: ProjectConfig, role: str) -> str:
    return config.source_preprocessor if role == ROLE_SOURCE else config.target_preprocessor


def compare_file_sets(
    config: ProjectConfig,
    role: str,
    kind: str,
    old: dict[str, str],
    new: dict[str, str],
    place_new,
    renames: dict[str, str] | None = None,
    strict: bool = False,
) -> SideChanges:
    """Compare two file sets of one side, each given as relative path -> content hash.

    Files are compared by path and hash. Only the files that differ are then
    split into elements, so a large side with one edited file costs one file's
    worth of work, and no model is asked anything. `place_new(paths, directory)`
    writes the new files that are needed. With `strict`, an old file whose
    bytes are gone raises FilesMissing rather than being left out.
    """
    # Only the files this kind is read from: a repository's .gitignore or
    # README is no change to its code.
    readable = tuple(ARTIFACT_KINDS_BY_KEY[kind].extensions)
    old = {path: digest for path, digest in old.items() if path.lower().endswith(readable)}
    new = {path: digest for path, digest in new.items() if path.lower().endswith(readable)}
    files = diff_files(old, new, renames)
    if files.added and files.removed:
        files = diff_files(old, new, {**same_text_moved(files, old, place_new), **(renames or {})})
    if not files.changed:
        return SideChanges(role=role, files=files, elements=diff_elements([], []))

    wanted = [(path, old[path]) for path in files.removed + files.modified + list(files.renamed)]
    with tempfile.TemporaryDirectory(prefix="tracerag-diff-") as scratch:
        before, after = Path(scratch) / "old", Path(scratch) / "new"
        if artifact_store.materialise(wanted, before) < len(wanted) and strict:
            raise FilesMissing("Some of the older version's files are no longer stored.")
        place_new(files.added + files.modified + list(files.renamed.values()), after)

        preprocessor = PreprocessorType(preprocessor_of(config, role))
        elements = diff_elements(
            read_items(kind, preprocessor, before),
            read_items(kind, preprocessor, after),
            files.renamed,
        )
    return SideChanges(role=role, files=files, elements=elements)


def same_text_moved(files, old: dict[str, str], place_new) -> dict[str, str]:
    """Removed files that reappear at a new path differing only in whitespace.

    A file checked out with CRLF line endings has other bytes than the same
    file uploaded with LF, so its hash cannot pair it with its old path.
    """
    with tempfile.TemporaryDirectory(prefix="tracerag-moved-") as scratch:
        before, after = Path(scratch) / "old", Path(scratch) / "new"
        artifact_store.materialise([(path, old[path]) for path in files.removed], before)
        place_new(files.added, after)

        def texts(directory: Path, paths: list[str]) -> dict[str, str]:
            return {
                path: element_hash((directory / path).read_text(encoding="utf-8", errors="ignore"))
                for path in paths if (directory / path).is_file()
            }

        return diff_files(texts(before, files.removed), texts(after, files.added)).renamed


def net_changes(
    config: ProjectConfig, base: ProjectVersion, head: ProjectVersion, between: list[ProjectVersion],
) -> list[SideChanges]:
    """The net change of each side from one version to a later one.

    Worked out again from the two versions' stored files rather than added up
    from what each version in between recorded: one comparison, and nothing
    in between to get wrong. The renames those versions recorded are still
    passed on, so a file renamed and then edited is recognised as renamed.
    `between` is every version after `base` up to `head`.
    """
    sides = []
    for role in (ROLE_SOURCE, ROLE_TARGET):
        old = next((a for a in base.artifacts if a.role == role), None)
        new = next((a for a in head.artifacts if a.role == role), None)
        if old is None or new is None:
            continue
        new_files = {file.relative_path: file.sha256 for file in new.files}

        def place_new(paths: list[str], directory: Path) -> None:
            if artifact_store.materialise([(path, new_files[path]) for path in paths], directory) < len(paths):
                raise FilesMissing("Some of the later version's files are no longer stored.")

        steps = [
            {item["old"]: item["new"] for item in
             json.loads(version.changes_json or "{}").get(role, {}).get("files", {}).get("renamed", [])}
            for version in between
        ]
        sides.append(compare_file_sets(
            config, role, new.artifact_type,
            {file.relative_path: file.sha256 for file in old.files}, new_files,
            place_new, chain_renames(steps), strict=True,
        ))
    return sides


def copy_from(directory: Path):
    """A `place_new` that copies the new files out of a directory on disk."""
    def place_new(paths: list[str], destination: Path) -> None:
        for path in paths:
            (destination / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / path, destination / path)
    return place_new


def line_diff(old: bytes, new: bytes) -> tuple[list[str], bool]:
    """Two versions of a text file as unified diff lines, and whether they were cut short."""
    lines = list(unified_diff(
        old.decode("utf-8", errors="replace").splitlines(),
        new.decode("utf-8", errors="replace").splitlines(),
        lineterm="", n=2,
    ))[2:]
    return lines[:MAX_DIFF_LINES], len(lines) > MAX_DIFF_LINES
