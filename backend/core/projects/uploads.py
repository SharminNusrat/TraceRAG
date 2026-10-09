"""Writing files someone handed over to disk, safely and within limits."""

import logging
import re
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 30 * 1024 * 1024

SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.\- ]+")


class UploadError(Exception):
    """Files that cannot be taken in. The message is safe to show a user."""


class UploadTooLarge(UploadError):
    """More bytes than one file, or one request, is allowed."""


class InvalidUpload(UploadError):
    """Files that are not what they claim to be."""


class UploadBudget:
    """Tracks bytes written across a whole request, not just per file."""

    def __init__(self, total_limit: int = MAX_TOTAL_UPLOAD_BYTES):
        self.total_limit = total_limit
        self.used = 0

    def consume(self, size: int, label: str) -> None:
        self.used += size
        if self.used > self.total_limit:
            limit_mb = self.total_limit // (1024 * 1024)
            raise UploadTooLarge(
                f"Upload exceeds the {limit_mb} MB total limit (adding '{label}')."
            )


def safe_relative_path(raw_path: str) -> Path:
    """Turn a browser-supplied relative path into a contained, sanitised path."""
    parts = []
    for segment in re.split(r"[\/]+", raw_path or ""):
        segment = segment.strip()
        if not segment or segment in {".", ".."}:
            continue
        parts.append(SAFE_SEGMENT.sub("-", segment))
    return Path(*parts) if parts else Path("file")


def save_upload(upload, destination: Path, budget: UploadBudget) -> int:
    """Stream an upload to disk, enforcing per-file and total size limits."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(destination, "wb") as target:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                raise UploadTooLarge(
                    f"'{upload.filename}' exceeds the {limit_mb} MB per-file limit."
                )
            budget.consume(len(chunk), upload.filename or "file")
            target.write(chunk)
    return written


def extract_archive(archive_path: Path, destination: Path, budget: UploadBudget) -> None:
    """Extract a zip, skipping entries that would escape the destination."""
    if not zipfile.is_zipfile(archive_path):
        raise InvalidUpload(f"'{archive_path.name}' is not a valid .zip archive.")

    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target_path = (destination / safe_relative_path(member.filename)).resolve()
            if not target_path.is_relative_to(resolved_destination):
                logger.warning(f"Skipping unsafe archive entry: {member.filename}")
                continue
            budget.consume(member.file_size, member.filename)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target_path, "wb") as target:
                shutil.copyfileobj(source, target)
    archive_path.unlink(missing_ok=True)
