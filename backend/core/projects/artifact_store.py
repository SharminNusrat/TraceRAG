"""On-disk home for uploaded artifacts.

    <storage>/uploads/<upload_id>/     a real tree, because the pipeline reads
                                       real files; reaped if nobody saves it
    <storage>/blobs/<ab>/<abcdef...>   claimed files, named by content hash so
                                       the same file is never stored twice

Which bytes belong to which artifact is a database question, answered by the
ArtifactFile rows - so there is no directory per analysis.
"""

import hashlib
import io
import json
import logging
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

STORAGE_ROOT = Path(settings.storage_path)
UPLOAD_ROOT = STORAGE_ROOT / "uploads"
BLOB_ROOT = STORAGE_ROOT / "blobs"
MANIFEST_NAME = "manifest.json"

# Upload ids come from the client, so they are matched against this before
# ever reaching the filesystem.
UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

READ_CHUNK = 1024 * 1024

# A blob is written before the row referencing it is committed, so only sweep
# what has been unreferenced for a while.
GC_GRACE_SECONDS = 3600


# ----- Pending uploads -----

def new_upload_id() -> str:
    # Unguessable: holding an id is what lets you claim the files.
    return secrets.token_urlsafe(16)


def upload_dir(upload_id: str) -> Path | None:
    """The pending directory for an id, or None if the id is not usable."""
    if not upload_id or not UPLOAD_ID_PATTERN.match(upload_id):
        return None

    candidate = (UPLOAD_ROOT / upload_id).resolve()
    # Belt and braces: the pattern already excludes separators and dots.
    if not candidate.is_relative_to(UPLOAD_ROOT.resolve()):
        return None
    return candidate


def create_upload_dir() -> tuple[str, Path]:
    upload_id = new_upload_id()
    path = UPLOAD_ROOT / upload_id
    path.mkdir(parents=True, exist_ok=True)
    return upload_id, path


def write_manifest(directory: Path, entries: list[dict]) -> None:
    """Record what each artifact was, so a claim can rebuild the rows."""
    # The pipeline only sees directories; an artifact's name, kind and side
    # live in the request and would otherwise be lost by save time.
    (directory / MANIFEST_NAME).write_text(json.dumps(entries, indent=2), encoding="utf-8")


def read_manifest(directory: Path) -> list[dict]:
    manifest = directory / MANIFEST_NAME
    if not manifest.exists():
        return []
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        return entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        logger.warning(f"Unreadable manifest at {manifest}")
        return []


def discard_upload(upload_id: str) -> None:
    """Drop a pending upload that will never be claimed."""
    path = upload_dir(upload_id)
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


def purge_expired_uploads() -> int:
    """Delete pending uploads past the retention window. Returns how many."""
    if not UPLOAD_ROOT.is_dir():
        return 0

    cutoff = time.time() - settings.upload_retention_hours * 3600
    removed = 0
    for entry in UPLOAD_ROOT.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            # A directory being written by another request; leave it for the
            # next sweep rather than failing the one that triggered this.
            continue

    if removed:
        logger.info(f"Purged {removed} unclaimed upload(s)")
    return removed


# ----- Content-addressed blobs -----

def blob_path(digest: str) -> Path:
    """Two-character fan-out, so no single directory holds every blob."""
    return BLOB_ROOT / digest[:2] / digest


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def store_blob(path: Path) -> tuple[str, int, bool]:
    """Store one file. Returns (digest, size, was_already_present)."""
    digest = hash_file(path)
    size = path.stat().st_size
    destination = blob_path(digest)

    if destination.exists():
        return digest, size, True

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target and rename, so a crash mid-copy cannot leave a
    # truncated file sitting at a name that claims to be that content.
    staging = destination.with_name(f"{digest}.{secrets.token_hex(4)}.part")
    shutil.copyfile(path, staging)
    staging.replace(destination)
    return digest, size, False


def collect_files(directory: Path) -> list[tuple[str, Path]]:
    """Every file under a directory as (relative posix path, absolute path)."""
    if not directory.is_dir():
        return []
    return sorted(
        (item.relative_to(directory).as_posix(), item)
        for item in directory.rglob("*")
        if item.is_file()
    )


def open_blob(digest: str) -> bytes | None:
    path = blob_path(digest)
    try:
        return path.read_bytes()
    except OSError:
        return None


def materialise(entries: list[tuple[str, str]], destination: Path) -> int:
    """Write blobs back out as a real tree, for re-running. Returns count."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    restored = 0

    for relative_path, digest in entries:
        # These paths come from our own rows, but they are still joined onto a
        # filesystem path, so they are checked rather than trusted.
        target = (destination / relative_path).resolve()
        if not target.is_relative_to(root):
            logger.warning(f"Skipping unsafe stored path: {relative_path}")
            continue

        content = open_blob(digest)
        if content is None:
            logger.warning(f"Blob {digest[:12]} missing for {relative_path}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        restored += 1

    return restored


def build_zip(entries: list[tuple[str, str]]) -> io.BytesIO:
    """Zip an artifact from (relative path, digest) pairs."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative_path, digest in entries:
            content = open_blob(digest)
            if content is not None:
                archive.writestr(relative_path, content)
    buffer.seek(0)
    return buffer


def collect_garbage(referenced: set[str]) -> int:
    """Delete blobs no artifact points at any more. Returns how many."""
    # Mark and sweep, not reference counting: the database already knows every
    # digest in use, and a separate count could drift out of step with it.
    if not BLOB_ROOT.is_dir():
        return 0

    cutoff = time.time() - GC_GRACE_SECONDS
    removed = 0
    for path in BLOB_ROOT.rglob("*"):
        try:
            if not path.is_file() or path.name in referenced:
                continue
            if path.stat().st_mtime >= cutoff:
                continue  # too new to be sure nobody is mid-save on it
            path.unlink()
            removed += 1
        except OSError:
            continue

    if removed:
        logger.info(f"Collected {removed} unreferenced blob(s)")
    return removed
