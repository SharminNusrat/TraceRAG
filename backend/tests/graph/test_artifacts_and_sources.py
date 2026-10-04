"""Taking a run's files in, and what that does to the project's sources."""

from sqlalchemy import select

from core.db.models import VersionSource
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


def sources(db, project, include_disconnected=True) -> dict[str, bool]:
    return {
        f"{source.kind}:{source.name}": source.is_active
        for source in service.list_sources(db, project, include_disconnected)
    }


@case(
    id="G-13",
    feature="Artifact storage / claim_artifacts",
    level="graph",
    priority="Critical",
    why="This is the exact cause of the multi-configuration sync bug: the first run's claim deleted the folder the next configuration still had to read.",
    input="Claim one upload with keep_files=True, and another with the default",
    expected="keep_files=True: 2 artifacts attached and the working folder still exists. Default: 2 attached and the folder is removed",
)
def test_claim_keeps_the_working_folder_only_when_asked(db, record):
    """A sync asks for the files to stay until every configuration has run; an ordinary save does not."""
    project = make_project(db)
    kept, removed = upload(), upload()

    first = save_run(db, project, EMPTY, upload_id=kept, keep_files=True)
    second = save_run(db, project, EMPTY, upload_id=removed)
    record(f"keep_files=True: {len(first.artifacts)} artifacts, folder exists: {artifact_store.upload_dir(kept).is_dir()}")
    record(f"default: {len(second.artifacts)} artifacts, folder exists: {artifact_store.upload_dir(removed).is_dir()}")

    assert len(first.artifacts) == len(second.artifacts) == 2
    assert artifact_store.upload_dir(kept).is_dir()
    assert not artifact_store.upload_dir(removed).is_dir()


@case(
    id="G-11",
    feature="Artifact storage / claim_artifacts",
    level="graph",
    priority="Critical",
    why="Claiming is where uploaded bytes become permanent. Every later re-run, sync and download reads what this stored.",
    input="An upload with 2 requirement files and 1 code file, claimed by a run in version 1",
    expected="2 artifacts with 3 files, each file's blob on disk; 2 active upload sources each with a 16-character fingerprint; version 1 records both sources",
)
def test_claim_stores_blobs_sources_and_seals_the_version(db, record):
    """Claiming an upload stores its files by content, creates the project's sources and records them on the version."""
    project = make_project(db)
    run = save_run(db, project, EMPTY, upload_id=upload())

    files = [file for artifact in run.artifacts for file in artifact.files]
    held = service.list_sources(db, project)
    sealed = db.execute(select(VersionSource).where(VersionSource.version_id == run.version_id)).scalars().all()
    record(f"artifacts: {[(a.role, a.artifact_type, a.file_count) for a in run.artifacts]}")
    record(f"blobs on disk: {sum(artifact_store.blob_exists(f.sha256) for f in files)} of {len(files)}")
    record(f"sources: {[(s.kind, s.name, s.origin, s.last_sync_ref) for s in held]}")
    record(f"sources recorded on the version: {len(sealed)}")

    assert sorted((a.role, a.artifact_type, a.file_count) for a in run.artifacts) == [
        ("source", "requirements", 2), ("target", "code", 1),
    ]
    assert all(artifact_store.blob_exists(file.sha256) for file in files)
    assert [(s.kind, s.origin, s.is_active) for s in held] == [
        ("requirements", "upload", True), ("code", "upload", True),
    ]
    assert all(len(s.last_sync_ref) == 16 for s in held)
    assert {row.source_id for row in sealed} == {s.source_id for s in held}
    assert {a.source_id for a in run.artifacts} == {s.source_id for s in held}


@case(
    id="G-12",
    feature="Sources / one source per kind",
    level="graph",
    priority="High",
    why="A kind has one source at a time. Two active sources of one kind leave a sync with no way to choose which to refresh.",
    preconditions="A project whose requirements source is 'reqs' and code source is 'code'",
    input="A second upload naming its requirements 'new reqs' (code unchanged); then an upload with requirements on both sides",
    expected="'reqs' is disconnected and 'new reqs' active, code source reused. With the same kind on both sides, both of those sources stay active",
)
def test_new_upload_of_a_kind_replaces_the_old_source(db, record):
    """Uploading a kind under a new name takes that kind over; two sides of one kind are the one exception."""
    project = make_project(db)
    save_run(db, project, EMPTY, upload_id=upload())
    save_run(db, project, EMPTY, upload_id=upload(requirements="new reqs"))
    after = sources(db, project)
    record(f"after the second upload: {after}")

    assert after == {"requirements:reqs": False, "code:code": True, "requirements:new reqs": True}

    both_sides = make_project(db)
    save_run(db, both_sides, EMPTY, kinds=("requirements", "requirements"), upload_id=make_upload({
        "source": ("requirements", "old SRS", {"a.txt": unique("a")}),
        "target": ("requirements", "new SRS", {"b.txt": unique("b")}),
    }))
    record(f"same kind on both sides: {sources(db, both_sides)}")
    assert sources(db, both_sides) == {"requirements:old SRS": True, "requirements:new SRS": True}


@case(
    id="G-14",
    feature="Artifact storage / copy_artifacts",
    level="graph",
    priority="High",
    why="A re-run and every later configuration in a sync point at the first run's files. They must share its blobs, not lose track of them.",
    preconditions="An analysis that claimed an upload",
    input="copy_artifacts from that analysis to a new one",
    expected="The new analysis has 2 artifacts with the same file hashes and the same source ids; no new blob is written",
)
def test_copied_artifacts_share_blobs_and_sources(db, record):
    """Copying artifacts adds rows that point at the same stored files."""
    project = make_project(db)
    original = save_run(db, project, EMPTY, upload_id=upload())
    blobs_before = sum(1 for p in artifact_store.BLOB_ROOT.rglob("*") if p.is_file())

    copy = save_run(db, project, EMPTY)
    copied = service.copy_artifacts(db, original, copy)
    blobs_after = sum(1 for p in artifact_store.BLOB_ROOT.rglob("*") if p.is_file())

    def fingerprint(analysis):
        return sorted((a.role, a.source_id, f.relative_path, f.sha256) for a in analysis.artifacts for f in a.files)

    record(f"artifacts copied: {copied}; blobs before {blobs_before}, after {blobs_after}")
    assert copied == 2
    assert fingerprint(copy) == fingerprint(original)
    assert blobs_before == blobs_after


@case(
    id="G-16",
    feature="Sources / disconnect_source",
    level="graph",
    priority="High",
    why="A source a version recorded is history: deleting it would make two versions read as if nothing ever changed between them.",
    input="Disconnect a source recorded by a version, and a GitHub source connected but never synced",
    expected="The recorded one is kept but inactive (returns False); the never-used one is deleted outright (returns True)",
)
def test_disconnect_keeps_recorded_sources_and_removes_unused_ones(db, record):
    """Disconnecting stands a source down, and only deletes it when no version ever recorded it."""
    project = make_project(db)
    save_run(db, project, EMPTY, upload_id=upload())
    recorded = service.active_source_for_kind(db, project, "requirements")
    unused = service.connect_github_source(
        db, project, kind="architecture", repository="owner/models", branch="main", name="models",
    )

    removed_recorded = service.disconnect_source(db, recorded)
    removed_unused = service.disconnect_source(db, unused)
    record(f"recorded source removed outright: {removed_recorded}; never-synced source removed outright: {removed_unused}")
    record(f"sources left (name: active): {sources(db, project)}")

    assert (removed_recorded, removed_unused) == (False, True)
    assert sources(db, project) == {"requirements:reqs": False, "code:code": True}
    assert service.versions_using(db, recorded) == 1


@case(
    id="G-15",
    feature="Sources / reconnect_source",
    level="graph",
    priority="High",
    why="Reconnecting must restore the one-source-per-kind rule in the other direction, or the project ends up with two active sources.",
    preconditions="Requirements source 'reqs' was replaced by 'new reqs'",
    input="reconnect_source on the old 'reqs' source",
    expected="'reqs' active again and 'new reqs' disconnected; the code source untouched",
)
def test_reconnect_takes_the_kind_back(db, record):
    """Reconnecting a source makes it the kind's only source again."""
    project = make_project(db)
    save_run(db, project, EMPTY, upload_id=upload())
    save_run(db, project, EMPTY, upload_id=upload(requirements="new reqs"))
    old = next(s for s in service.list_sources(db, project, True) if s.name == "reqs")

    service.reconnect_source(db, project, old)
    record(f"after reconnecting 'reqs': {sources(db, project)}")

    assert sources(db, project) == {"requirements:reqs": True, "code:code": True, "requirements:new reqs": False}
    assert service.active_source_for_kind(db, project, "requirements").source_id == old.source_id


@case(
    id="G-17",
    feature="Pairs / list_pairs and side_is_behind",
    level="graph",
    priority="High",
    why="A pair is what gets synced. If 'out of date' is wrong, a user either re-runs for nothing or trusts links computed on old requirements.",
    preconditions="A project with requirements->code run on one set of files",
    input="A second pair, requirements->architecture, uploaded with changed requirements under the same source name",
    expected="Two pairs listed in the order first run. The requirements side of the first pair is now behind; its code side and the new pair are not",
)
def test_pair_falls_behind_when_a_shared_source_moves_on(db, record):
    """Each pair of kinds is tracked separately, and knows when a source it shares has newer files."""
    project = make_project(db)
    first = save_run(db, project, EMPTY, upload_id=upload())
    assert not any(service.side_is_behind(db, project, a) for a in first.artifacts)

    second = save_run(db, project, EMPTY, kinds=("requirements", "architecture"), upload_id=make_upload({
        "source": ("requirements", "reqs", {"UC1.txt": unique("changed")}),
        "target": ("architecture", "model", {"model.uml": unique("model")}),
    }))

    behind = {a.artifact_type: service.side_is_behind(db, project, a) for a in first.artifacts}
    record(f"pairs: {service.list_pairs(db, project)}")
    record(f"first pair behind, by kind: {behind}")
    record(f"second pair behind: {[service.side_is_behind(db, project, a) for a in second.artifacts]}")

    assert service.list_pairs(db, project) == [("requirements", "code"), ("requirements", "architecture")]
    assert behind == {"requirements": True, "code": False}
    assert not any(service.side_is_behind(db, project, a) for a in second.artifacts)
    assert service.latest_pair_analysis(db, project, "requirements", "code").analysis_id == first.analysis_id
    assert service.latest_pair_analysis(db, project, "code", "requirements") is None
