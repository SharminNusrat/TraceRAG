"""Writing uploaded files to disk: names made safe, sizes kept within limits."""

import io
import zipfile

import pytest

from core.projects import uploads
from core.projects.uploads import (
    InvalidUpload, UploadBudget, UploadTooLarge, extract_archive, safe_relative_path, save_upload,
)
from tests.recorder import case


class Upload:
    """What a web framework hands over for one uploaded file."""

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self.file = io.BytesIO(data)


def zip_of(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def files_under(folder) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


@case(
    id="U-08",
    feature="Upload safety / extract_archive",
    level="unit",
    priority="High",
    why="A zip entry named '../x' is the classic way an upload writes outside its folder and overwrites server files.",
    input="A zip with entries 'src/A.java', '../evil.java', '../../deep/evil.java' and '/abs.java'",
    expected="All four files end up inside the destination; nothing is written outside it; the archive is removed",
)
def test_zip_entries_cannot_escape_the_destination(tmp_path, record):
    """Entries that try to climb out of the destination are kept inside it."""
    archive = tmp_path / "upload" / "code.zip"
    archive.parent.mkdir()
    archive.write_bytes(zip_of({
        "src/A.java": b"class A {}",
        "../evil.java": b"x",
        "../../deep/evil.java": b"x",
        "/abs.java": b"x",
    }))
    destination = tmp_path / "upload" / "extracted"

    extract_archive(archive, destination, UploadBudget())

    inside = files_under(destination)
    outside = [p for p in files_under(tmp_path) if not p.startswith("upload/extracted/")]
    record(f"inside the destination: {inside}")
    record(f"outside the destination: {outside}")

    assert inside == ["abs.java", "deep/evil.java", "evil.java", "src/A.java"]
    assert outside == []
    assert not archive.exists()


@case(
    id="U-05",
    feature="Upload safety / safe_relative_path",
    level="unit",
    priority="High",
    why="File paths come from the browser. One that survives as '../' or as an absolute path can write anywhere on the server.",
    input="'a/b.txt', '../../etc/passwd', '/etc/passwd', 'C:\\\\Windows\\\\x.txt', 'a\\\\..\\\\b', './a/./b', '  /x//y ', 'we!rd$name.java', ''",
    expected="Every result is relative, has no '..' part, and resolves inside the destination; '' becomes 'file'",
)
def test_browser_paths_are_made_safe(tmp_path, record):
    """Whatever path is supplied, the result stays inside the folder it is written to."""
    raw_paths = [
        "a/b.txt", "../../etc/passwd", "/etc/passwd", "C:\\Windows\\x.txt",
        "a\\..\\b", "./a/./b", "  /x//y ", "we!rd$name.java", "",
    ]
    cleaned = {raw: safe_relative_path(raw) for raw in raw_paths}
    record({raw: path.as_posix() for raw, path in cleaned.items()})

    for raw, path in cleaned.items():
        assert not path.is_absolute(), raw
        assert ".." not in path.parts, raw
        assert (tmp_path / path).resolve().is_relative_to(tmp_path.resolve()), raw

    assert cleaned["a/b.txt"].as_posix() == "a/b.txt"
    assert cleaned["../../etc/passwd"].as_posix() == "etc/passwd"
    assert cleaned[""].as_posix() == "file"


@case(
    id="U-06",
    feature="Upload safety / UploadBudget",
    level="unit",
    priority="Medium",
    why="The total limit is what stops one request from filling the disk with many files that are each under the per-file limit.",
    input="A budget of 10 bytes: consume 6, then 4, then 1 more",
    expected="The first two are accepted; the third raises UploadTooLarge naming the file",
)
def test_total_upload_budget_is_enforced(record):
    """Bytes are counted across the whole request, and the limit is exact."""
    budget = UploadBudget(total_limit=10)
    budget.consume(6, "a.txt")
    budget.consume(4, "b.txt")
    record(f"used after two files: {budget.used} of {budget.total_limit}")

    with pytest.raises(UploadTooLarge) as refusal:
        budget.consume(1, "c.txt")
    record(f"third file: {refusal.value}")

    assert "c.txt" in str(refusal.value)


@case(
    id="U-07",
    feature="Upload safety / save_upload",
    level="unit",
    priority="Medium",
    why="A single oversized file must be stopped while it is streamed, not after it has been written in full.",
    preconditions="Per-file limit lowered to 1,000 bytes for the test (30 MB in production)",
    input="A 600-byte file, then a 1,500-byte file",
    expected="The first is saved and returns 600; the second raises UploadTooLarge naming the file",
)
def test_per_file_limit_is_enforced(tmp_path, monkeypatch, record):
    """A file over the per-file limit is refused."""
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)

    written = save_upload(Upload("small.txt", b"x" * 600), tmp_path / "small.txt", UploadBudget())
    record(f"600-byte file: saved {written} bytes")

    with pytest.raises(UploadTooLarge) as refusal:
        save_upload(Upload("big.txt", b"x" * 1500), tmp_path / "big.txt", UploadBudget())
    record(f"1,500-byte file: {refusal.value}")

    assert written == 600
    assert "big.txt" in str(refusal.value)


@case(
    id="U-09",
    feature="Upload safety / extract_archive",
    level="unit",
    priority="Medium",
    why="A file that is not a zip, or a small zip that unpacks to something huge, must be refused cleanly.",
    input="A file of plain text named .zip; a valid zip holding 500 bytes, extracted with a 100-byte budget",
    expected="InvalidUpload for the first; UploadTooLarge for the second",
)
def test_bad_and_oversized_archives_are_refused(tmp_path, record):
    """An archive is checked before it is trusted, and what it unpacks to counts against the budget."""
    fake = tmp_path / "notes.zip"
    fake.write_bytes(b"this is not a zip")
    with pytest.raises(InvalidUpload) as invalid:
        extract_archive(fake, tmp_path / "a", UploadBudget())
    record(f"not a zip: {invalid.value}")

    real = tmp_path / "big.zip"
    real.write_bytes(zip_of({"data.java": b"x" * 500}))
    with pytest.raises(UploadTooLarge) as too_large:
        extract_archive(real, tmp_path / "b", UploadBudget(total_limit=100))
    record(f"unpacks past the budget: {too_large.value}")

    assert "notes.zip" in str(invalid.value)
