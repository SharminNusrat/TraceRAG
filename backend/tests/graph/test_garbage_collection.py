"""Deleting things: which stored files may go, and which must not."""

import os
import time

from sqlalchemy import func, select

from core.db.models import Analysis, ElementLink, GraphEdge, GraphNode, ProjectConfig, ProjectSource
from core.projects import artifact_store, service
from tests.helpers import make_project, make_upload, result_of, save_run, unique
from tests.recorder import case

RESULT = result_of(["UC1.txt"], ["Auth.java::login()"], [("UC1.txt", "Auth.java::login()", 0.9)])
TWO_HOURS = 2 * 3600


def age(digests) -> None:
    """Make blobs look old enough to collect: the collector never touches a fresh one."""
    old = time.time() - TWO_HOURS
    for digest in digests:
        os.utime(artifact_store.blob_path(digest), (old, old))


def digests_of(analysis) -> list[str]:
    return [file.sha256 for artifact in analysis.artifacts for file in artifact.files]


def upload_with(files: int = 2) -> str:
    return make_upload({
        "source": ("requirements", "reqs", {f"UC{n}.txt": unique("requirement") for n in range(files)}),
        "target": ("code", "code", {"Auth.java": unique("class Auth")}),
    })


def keep_store_busy(db) -> None:
    """Another project holding plenty of files, so a small sweep is a small share of the store."""
    save_run(db, make_project(db), RESULT, upload_id=upload_with(files=8))


@case(
    id="G-18",
    feature="Garbage collection / shared blobs",
    level="graph",
    priority="Critical",
    why="A re-run shares its original's files. Deleting the original must not delete files the re-run still needs, or it can never be re-run or downloaded again.",
    preconditions="An analysis and a re-run of it that share the same 3 stored files, all older than the collector's grace period",
    input="Delete the original analysis; then delete the re-run as well",
    expected="After the first delete all 3 files remain. After the second, nothing references them and they are retired to the trash folder",
)
def test_shared_files_survive_until_the_last_analysis_is_deleted(db, record):
    """Stored files are only collected once no analysis references them."""
    keep_store_busy(db)
    project = make_project(db)
    original = save_run(db, project, RESULT, upload_id=upload_with())
    rerun = save_run(db, project, RESULT)
    service.copy_artifacts(db, original, rerun)
    digests = digests_of(original)
    age(digests)

    service.delete_analysis(db, original)
    after_first = [artifact_store.blob_path(d).exists() for d in digests]
    record(f"after deleting the original: {sum(after_first)} of {len(digests)} files still stored")

    service.delete_analysis(db, db.get(Analysis, rerun.analysis_id))
    after_second = [artifact_store.blob_path(d).exists() for d in digests]
    in_trash = [(artifact_store.TRASH_ROOT / d).exists() for d in digests]
    record(f"after deleting the re-run: {sum(after_second)} still stored, {sum(in_trash)} retired to trash")

    assert all(after_first)
    assert not any(after_second) and all(in_trash)


@case(
    id="G-19",
    feature="Garbage collection / safety refusals",
    level="graph",
    priority="Critical",
    why="The collector deletes files on the database's word. Pointed at an empty or wrong database, it would wipe every stored artifact.",
    preconditions="A private store of 4 old files and 1 file written just now",
    input="collect_garbage told that: nothing is referenced; only 1 of 4 is referenced; 3 of 4 are referenced",
    expected="Refuses the first (0 swept) and the second (3 of 5 is most of the store, 0 swept). Sweeps exactly 1 in the third. The fresh file is never touched",
)
def test_collector_refuses_a_sweep_that_cannot_be_right(tmp_path, monkeypatch, record):
    """A sweep that would take most of the store, or that no database row justifies, is refused."""
    monkeypatch.setattr(artifact_store, "BLOB_ROOT", tmp_path / "blobs")
    monkeypatch.setattr(artifact_store, "TRASH_ROOT", tmp_path / "trash")
    old = time.time() - TWO_HOURS
    for name in ("a", "b", "c", "d", "fresh"):
        path = tmp_path / "blobs" / name[0] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        if name != "fresh":
            os.utime(path, (old, old))

    def stored():
        return sorted(p.name for p in (tmp_path / "blobs").rglob("*") if p.is_file())

    swept = {
        "nothing referenced": artifact_store.collect_garbage(set()),
        "1 of 4 referenced": artifact_store.collect_garbage({"a"}),
    }
    assert stored() == ["a", "b", "c", "d", "fresh"]

    swept["3 of 4 referenced"] = artifact_store.collect_garbage({"a", "b", "c"})
    record(f"files swept: {swept}")
    record(f"left in the store: {stored()}; in trash: {[p.name for p in (tmp_path / 'trash').iterdir()]}")

    assert swept == {"nothing referenced": 0, "1 of 4 referenced": 0, "3 of 4 referenced": 1}
    assert stored() == ["a", "b", "c", "fresh"]
    assert [p.name for p in (tmp_path / "trash").iterdir()] == ["d"]


@case(
    id="G-25",
    feature="Garbage collection / delete_project",
    level="graph",
    priority="High",
    why="Deleting a project must remove everything it owned and nothing anyone else owns: no orphan rows, and no other project's files.",
    preconditions="Two projects that uploaded byte-identical files (so they share blobs), each with a graph and stored pins",
    input="Delete the first project",
    expected="Its analyses, sources, configurations, graph nodes, edges and pins are all gone; the second project's rows and its files are untouched",
)
def test_deleting_a_project_removes_its_rows_but_not_shared_files(db, record):
    """A project's rows cascade away with it; files another project also holds stay."""
    keep_store_busy(db)
    files = {
        "source": ("requirements", "reqs", {"UC1.txt": unique("same text")}),
        "target": ("code", "code", {"Auth.java": unique("same class")}),
    }
    doomed, survivor = make_project(db), make_project(db)
    gone = save_run(db, doomed, RESULT, upload_id=make_upload(files))
    kept = save_run(db, survivor, RESULT, upload_id=make_upload(files))
    digests = digests_of(kept)
    assert sorted(digests) == sorted(digests_of(gone))
    age(digests)
    config, project_id = gone.config_id, doomed.project_id

    service.delete_project(db, doomed)

    def count(model, column, value):
        return db.scalar(select(func.count()).select_from(model).where(column == value))

    left = {
        "analyses": count(Analysis, Analysis.project_id, project_id),
        "sources": count(ProjectSource, ProjectSource.project_id, project_id),
        "configurations": count(ProjectConfig, ProjectConfig.project_id, project_id),
        "graph nodes": count(GraphNode, GraphNode.config_id, config),
        "graph edges": count(GraphEdge, GraphEdge.config_id, config),
        "pins": count(ElementLink, ElementLink.config_id, config),
    }
    record(f"rows left for the deleted project: {left}")
    record(f"survivor: {len(service.list_analyses(db, survivor.user_id, survivor.project_id))} analysis, "
           f"graph {service.graph_summary(db, kept.config_id)}, "
           f"files stored {sum(artifact_store.blob_exists(d) for d in digests)} of {len(digests)}")

    assert set(left.values()) == {0}
    assert service.graph_summary(db, kept.config_id)["links_active"] == 1
    assert all(artifact_store.blob_exists(d) for d in digests)
