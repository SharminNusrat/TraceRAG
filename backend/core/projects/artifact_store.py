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

STORAGE_ROOT = settings.storage_root
UPLOAD_ROOT = STORAGE_ROOT / "uploads"
BLOB_ROOT = STORAGE_ROOT / "blobs"
TRASH_ROOT = STORAGE_ROOT / "trash"
MANIFEST_NAME = "manifest.json"

# Upload ids come from the client, so they are matched against this before
# ever reaching the filesystem.
UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

READ_CHUNK = 1024 * 1024

# A blob is written before the row referencing it is committed, so only sweep
# what has been unreferenced for a while.
GC_GRACE_SECONDS = 3600

# Retired blobs wait here before being deleted for good. The collector decides
# what is garbage by asking the database, so a database that is empty, freshly
# migrated, or simply not the right one makes every blob look unreferenced -
# which is how an analysis lost its files once already. Retiring instead of
# deleting makes that recoverable rather than final.
TRASH_RETENTION_DAYS = 7
# A sweep that would take most of the store is not garbage collection, it is a
# symptom. Refuse it and say so rather than acting on an answer that cannot be
# right.
MAX_SWEEP_FRACTION = 0.5


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
        # Something still wants a blob that was swept, so the sweep was wrong.
        # Put it back rather than leaving it in the bin to expire.
        return _unretire(digest)


def _unretire(digest: str) -> bytes | None:
    retired = TRASH_ROOT / digest
    if not retired.is_file():
        return None

    destination = blob_path(digest)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        retired.replace(destination)
        logger.warning(f"Restored retired blob {digest[:12]} - it was still referenced")
        return destination.read_bytes()
    except OSError as error:
        logger.warning(f"Could not restore retired blob {digest[:12]}: {error}")
        return None


def blob_exists(digest: str) -> bool:
    """Whether the bytes for a digest can still be served."""
    return blob_path(digest).is_file() or (TRASH_ROOT / digest).is_file()


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
    """Retire blobs no artifact points at any more. Returns how many.

    Mark and sweep, not reference counting: the database already knows every
    digest in use, and a separate count could drift out of step with it. But
    that makes the sweep only as trustworthy as the answer it is given, so an
    answer that cannot be right is refused rather than acted on, and what is
    swept is retired rather than deleted.
    """
    if not BLOB_ROOT.is_dir():
        return 0

    cutoff = time.time() - GC_GRACE_SECONDS
    blobs = [path for path in BLOB_ROOT.rglob("*") if path.is_file()]
    # Too new to be sure nobody is mid-save on it.
    stale = [
        path for path in blobs
        if path.name not in referenced and _modified_before(path, cutoff)
    ]
    if not stale:
        return 0

    if not referenced:
        logger.error(
            f"Refusing to sweep {len(stale)} blob(s): the database references none "
            f"at all. An empty database does not own a full artifact store - check "
            f"DATABASE_URL points at the right one."
        )
        return 0

    if len(stale) > len(blobs) * MAX_SWEEP_FRACTION:
        logger.error(
            f"Refusing to sweep {len(stale)} of {len(blobs)} blob(s): that is most "
            f"of the store, so the database being consulted is probably not the one "
            f"these files belong to."
        )
        return 0

    retired = sum(1 for path in stale if _retire(path))
    if retired:
        logger.info(
            f"Retired {retired} unreferenced blob(s) to {TRASH_ROOT.name}/, "
            f"deleted after {TRASH_RETENTION_DAYS} days"
        )
    return retired


def _modified_before(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _retire(path: Path) -> bool:
    """Move a blob to the bin, where a mistake can still be undone."""
    try:
        TRASH_ROOT.mkdir(parents=True, exist_ok=True)
        path.replace(TRASH_ROOT / path.name)
        return True
    except OSError as error:
        logger.warning(f"Could not retire blob {path.name[:12]}: {error}")
        return False


def purge_trash() -> int:
    """Delete retired blobs nobody came back for. Returns how many."""
    if not TRASH_ROOT.is_dir():
        return 0

    cutoff = time.time() - TRASH_RETENTION_DAYS * 86400
    removed = 0
    for path in TRASH_ROOT.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue

    if removed:
        logger.info(f"Deleted {removed} retired blob(s) past the {TRASH_RETENTION_DAYS}-day window")
    return removed
