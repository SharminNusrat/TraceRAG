"""Taking a version's files in, and what that does to the analysis's sides."""

from sqlalchemy import func, select

from core.db.models import ArtifactFile
from core.projects import artifact_store, service
from tests.helpers import make_project, make_upload, result_of, save_run, unique
from tests.recorder import case

EMPTY = result_of(["UC1.txt"], ["Auth.java"], [])


def upload(requirements="reqs", code="code", marker=None) -> str:
    """Two requirement files and one code file. `marker` makes the contents unique."""
    marker = marker or unique("content")
    return make_upload({
        "source": ("requirements", requirements, {"UC1.txt": f"login {marker}", "UC2.txt": f"logout {marker}"}),
        "target": ("code", code, {"src/Auth.java": f"class Auth {{}} // {marker}"}),
    })


def file_rows(db) -> int:
    return db.scalar(select(func.count()).select_from(ArtifactFile))


@case(
    id="G-11",
    feature="Artifact storage / claim_artifacts",
    level="graph",
    priority="Critical",
    why="Claiming is where uploaded bytes become permanent and where an analysis gets its two sides. Every later re-run, update and download reads what this stored.",
    input="An upload with 2 requirement files and 1 code file, claimed for version 1 of a New Analysis",
    expected="Version 1 holds 2 artifacts with 3 files, each file's blob on disk and each artifact an upload with a 16-character fingerprint; the analysis has a source side (requirements) and a target side (code); the working folder is removed",
)
def test_claim_stores_the_version_files_and_the_sides(db, record):
    """Claiming an upload stores its files by content as the version's file sets, and creates the analysis's sides."""
    project = make_project(db)
    pending = upload()
    run = save_run(db, project, EMPTY, upload_id=pending)

    artifacts = run.version.artifacts
    files = [file for artifact in artifacts for file in artifact.files]
    sides = service.list_sides(db, run.config)
    record(f"version {run.version.version_number} artifacts: {[(a.role, a.artifact_type, a.file_count, a.origin, a.ref) for a in artifacts]}")
    record(f"blobs on disk: {sum(artifact_store.blob_exists(f.sha256) for f in files)} of {len(files)}")
    record(f"sides: {[(s.role, s.kind, s.name, s.origin) for s in sides]}")
    record(f"working folder left: {artifact_store.upload_dir(pending).is_dir()}")

    assert sorted((a.role, a.artifact_type, a.file_count, a.origin) for a in artifacts) == [
        ("source", "requirements", 2, "upload"), ("target", "code", 1, "upload"),
    ]
    assert all(len(a.ref) == 16 for a in artifacts)
    assert all(artifact_store.blob_exists(file.sha256) for file in files)
    assert [(s.role, s.kind, s.origin) for s in sides] == [
        ("source", "requirements", "upload"), ("target", "code", "upload"),
    ]
    assert run.artifacts == artifacts
    assert not artifact_store.upload_dir(pending).is_dir()


@case(
    id="G-14",
    feature="Artifact storage / files belong to the version",
    level="graph",
    priority="High",
    why="Every run of a version reads the same files. A re-run that copied the file list would make two lists that can drift apart, and pay rows for nothing.",
    preconditions="A run that claimed an upload in version 1",
    input="A re-run in the same analysis and version",
    expected="The re-run reports the same 2 artifacts as the first run; no file row and no blob is added",
)
def test_a_rerun_reads_its_version_files_without_copying_them(db, record):
    """Runs of one version share that version's files: a re-run adds a run, not files."""
    project = make_project(db)
    original = save_run(db, project, EMPTY, upload_id=upload())
    rows_before = file_rows(db)
    blobs_before = sum(1 for p in artifact_store.BLOB_ROOT.rglob("*") if p.is_file())

    rerun = save_run(db, project, EMPTY, config=original.config, version=original.version)
    rows_after = file_rows(db)
    blobs_after = sum(1 for p in artifact_store.BLOB_ROOT.rglob("*") if p.is_file())
    record(f"artifacts: first run {[a.artifact_id for a in original.artifacts]}, re-run {[a.artifact_id for a in rerun.artifacts]}")
    record(f"file rows before {rows_before}, after {rows_after}; blobs before {blobs_before}, after {blobs_after}")

    assert [a.artifact_id for a in rerun.artifacts] == [a.artifact_id for a in original.artifacts]
    assert len(rerun.artifacts) == 2
    assert rows_before == rows_after and blobs_before == blobs_after


@case(
    id="G-27",
    feature="Sides / one analysis's sides are its own",
    level="graph",
    priority="Critical",
    why="The old model shared one source per kind across a project, so a new upload for one relation silently replaced the files of another. Each analysis now owns its sides and its files.",
    input="Two New Analyses in one project, both uploading requirements (under different names) traced to code",
    expected="Each analysis has its own source and target side; the first analysis's sides and the files its version holds are the same after the second upload",
)
def test_two_analyses_never_share_a_side(db, record):
    """A second analysis's upload leaves the first analysis's sides and files exactly as they were."""
    project = make_project(db)
    first = save_run(db, project, EMPTY, upload_id=upload())

    def state(run):
        return (
            [(s.source_id, s.name) for s in service.list_sides(db, run.config)],
            [(a.role, a.ref) for a in service.latest_version(db, run.config).artifacts],
        )

    before = state(first)
    second = save_run(db, project, EMPTY, upload_id=upload(requirements="second draft"))
    after = state(first)
    theirs = [(s.source_id, s.name) for s in service.list_sides(db, second.config)]
    record(f"first analysis (sides, files) before: {before}")
    record(f"first analysis (sides, files) after the second upload: {after}")
    record(f"second analysis's sides: {theirs}")

    assert before == after
    assert {s for s, _ in before[0]}.isdisjoint({s for s, _ in theirs})
    assert [name for _, name in theirs] == ["second draft", "code"]
