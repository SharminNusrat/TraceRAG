"""The living graph: what a link's status says after each run."""

from core.projects import service
from tests.helpers import graph_edges, make_project, result_of, save_run
from tests.recorder import case

REQUIREMENTS = ["UC1.txt", "UC2.txt"]
METHODS = ["Auth.java::Auth::login()", "Auth.java::Auth::logout()"]
LOGIN = ("UC1.txt", "Auth.java::Auth::login()")
LOGOUT = ("UC2.txt", "Auth.java::Auth::logout()")
BOTH = [(*LOGIN, 0.9), (*LOGOUT, 0.8)]


def describe(db, config_id) -> dict:
    return {
        f"{source} -> {target.split('::')[-1]}": edge.status
        for (source, target), edge in graph_edges(db, config_id).items()
    }


@case(
    id="G-01",
    feature="Living graph / update_graph",
    level="graph",
    priority="Critical",
    why="The first run is what every later status is measured against. Wrong version stamps here make all history wrong.",
    input="A first run in version 1: 2 requirements, 2 methods, 2 links",
    expected="4 nodes present, 2 links active, and each link's first_seen and last_verified are version 1",
)
def test_first_run_makes_every_link_active(db, record):
    """A first run records its elements as present and its links as active, stamped with its version."""
    project = make_project(db)
    run = save_run(db, project, result_of(REQUIREMENTS, METHODS, BOTH))

    summary = service.graph_summary(db, run.config_id)
    edges = graph_edges(db, run.config_id)
    record(f"summary: {summary}")
    record(f"statuses: {describe(db, run.config_id)}")

    assert summary == {"nodes_present": 4, "nodes_gone": 0, "links_active": 2, "links_stale": 0, "links_broken": 0}
    for edge in edges.values():
        assert edge.status == "active"
        assert edge.first_seen_version_id == edge.last_verified_version_id == run.version_id


@case(
    id="G-02",
    feature="Living graph / update_graph",
    level="graph",
    priority="Critical",
    why="'Stale' tells a user the classifier stopped finding a link whose two ends still exist. Mixing it up with 'broken' misreports what changed.",
    preconditions="Version 1 has links UC1->login and UC2->logout",
    input="Version 2: the same 4 elements, but only UC1->login is reported",
    expected="UC1->login active and verified at v2; UC2->logout stale, still verified at v1, first seen at v1; no node gone",
)
def test_link_no_longer_found_becomes_stale(db, record):
    """A link the latest run did not report, between two elements that still exist, is stale - not broken, not deleted."""
    project = make_project(db)
    first = save_run(db, project, result_of(REQUIREMENTS, METHODS, BOTH))
    second = save_run(db, project, result_of(REQUIREMENTS, METHODS, [(*LOGIN, 0.9)]))

    edges = graph_edges(db, first.config_id)
    record(f"statuses after version 2: {describe(db, first.config_id)}")
    record(f"summary: {service.graph_summary(db, first.config_id)}")

    assert edges[LOGIN].status == "active" and edges[LOGIN].last_verified_version_id == second.version_id
    assert edges[LOGOUT].status == "stale"
    assert edges[LOGOUT].first_seen_version_id == first.version_id
    assert edges[LOGOUT].last_verified_version_id == first.version_id
    assert service.graph_summary(db, first.config_id)["nodes_gone"] == 0


@case(
    id="G-03",
    feature="Living graph / update_graph",
    level="graph",
    priority="Critical",
    why="'Broken' is the one status that always means something is wrong: a requirement whose code is gone. It must be raised, and never lost.",
    preconditions="Version 1 has links UC1->login and UC2->logout",
    input="Version 2: the logout() method no longer exists; UC2 has no link",
    expected="UC2->logout is broken and kept; the logout node is marked gone (1 node gone); UC1->login stays active",
)
def test_link_to_a_removed_element_becomes_broken(db, record):
    """When one end of a link disappears, the link is kept and marked broken."""
    project = make_project(db)
    first = save_run(db, project, result_of(REQUIREMENTS, METHODS, BOTH))
    save_run(db, project, result_of(REQUIREMENTS, METHODS[:1], [(*LOGIN, 0.9)]))

    edges = graph_edges(db, first.config_id)
    summary = service.graph_summary(db, first.config_id)
    record(f"statuses after version 2: {describe(db, first.config_id)}")
    record(f"summary: {summary}")

    assert edges[LOGOUT].status == "broken"
    assert edges[LOGIN].status == "active"
    assert summary["nodes_gone"] == 1 and summary["links_broken"] == 1


@case(
    id="G-04",
    feature="Living graph / update_graph",
    level="graph",
    priority="High",
    why="History must survive a link going away and coming back: when it was first seen is a fact that a later run must not rewrite.",
    preconditions="UC2->logout was active in version 1 and stale in version 2",
    input="Version 3 reports UC2->logout again, with a new confidence of 0.75",
    expected="Active again, last_verified = v3, first_seen still v1, confidence updated to 0.75; still one row for that link",
)
def test_stale_link_found_again_keeps_its_history(db, record):
    """A link that comes back is the same link: reactivated, with its first sighting unchanged."""
    project = make_project(db)
    first = save_run(db, project, result_of(REQUIREMENTS, METHODS, BOTH))
    save_run(db, project, result_of(REQUIREMENTS, METHODS, [(*LOGIN, 0.9)]))
    third = save_run(db, project, result_of(REQUIREMENTS, METHODS, [(*LOGIN, 0.9), (*LOGOUT, 0.75)]))

    edges = graph_edges(db, first.config_id)
    edge = edges[LOGOUT]
    record(
        f"UC2->logout: status {edge.status}, confidence {edge.confidence}, "
        f"first seen in the first run's version: {edge.first_seen_version_id == first.version_id}, "
        f"last verified in the third run's version: {edge.last_verified_version_id == third.version_id}"
    )
    record(f"links stored for this configuration: {len(edges)}")

    assert edge.status == "active"
    assert edge.first_seen_version_id == first.version_id
    assert edge.last_verified_version_id == third.version_id
    assert edge.confidence == 0.75 and len(edges) == 2


@case(
    id="G-05",
    feature="Living graph / rename handling",
    level="graph",
    priority="High",
    why="Moving a file changes every identifier in it. Without following the rename, an ordinary refactor shows every link on that file as broken.",
    preconditions="Version 1 has links to methods in Auth.java",
    input="Version 2: Auth.java moved to security/Auth.java, with the rename map {'Auth.java': 'security/Auth.java'}; then the same move without the map, in a second project",
    expected="With the map: both links active on the new path, no node gone, first_seen still v1. Without it: 2 links broken and 2 new ones created",
)
def test_renamed_file_keeps_its_links(db, record):
    """Elements follow their file to its new path, so their links stay active instead of breaking."""
    moved = [name.replace("Auth.java", "security/Auth.java") for name in METHODS]
    moved_links = [("UC1.txt", moved[0], 0.9), ("UC2.txt", moved[1], 0.8)]

    followed = make_project(db)
    first = save_run(db, followed, result_of(REQUIREMENTS, METHODS, BOTH))
    save_run(db, followed, result_of(REQUIREMENTS, moved, moved_links),
             renames={"Auth.java": "security/Auth.java"})
    with_map = service.graph_summary(db, first.config_id)
    edges = graph_edges(db, first.config_id)
    record(f"with the rename map: {with_map}")
    record(f"links now on: {sorted(target for _, target in edges)}")

    lost = make_project(db)
    blind = save_run(db, lost, result_of(REQUIREMENTS, METHODS, BOTH))
    save_run(db, lost, result_of(REQUIREMENTS, moved, moved_links))
    without_map = service.graph_summary(db, blind.config_id)
    record(f"without the rename map: {without_map}")

    assert with_map["links_active"] == 2 and with_map["links_broken"] == 0 and with_map["nodes_gone"] == 0
    assert sorted(target for _, target in edges) == sorted(moved)
    assert all(edge.first_seen_version_id == first.version_id for edge in edges.values())
    assert without_map["links_broken"] == 2 and without_map["links_active"] == 2
