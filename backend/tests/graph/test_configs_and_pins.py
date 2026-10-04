"""Configurations, versions, and the links kept for the next run to re-offer."""

from sqlalchemy import func, select

from core.db.models import ElementLink, ProjectConfig
from core.projects import service
from tests.helpers import CONFIG, make_project, result_of, save_run
from tests.recorder import case

PAIRS = [("UC1.txt", "Auth.java::Auth::login()"), ("UC1.txt", "Auth.java::Auth::logout()")]


def stored_pins(db, config_id) -> int:
    return db.scalar(select(func.count()).select_from(ElementLink).where(ElementLink.config_id == config_id))


@case(
    id="G-06",
    feature="Link pinning / record_element_links",
    level="graph",
    priority="Critical",
    why="These rows are what the next sync re-offers to the classifier. Lose them and links silently drop out; keep stale ones and dead pairs are proposed forever.",
    preconditions="A configuration with 2 stored pairs",
    input="Record, in turn: the 2 pairs again with a duplicate; None; one new pair; an empty list",
    expected="Duplicate collapsed (2 rows); None leaves the 2 rows alone; a new list replaces them (1 row); an empty list clears them (0 rows)",
)
def test_pins_are_replaced_each_run_and_only_cleared_on_purpose(db, record):
    """What is stored is always the latest run's pairs; 'no list' and 'empty list' mean different things."""
    run = save_run(db, make_project(db), result_of(["UC1.txt"], [t for _, t in PAIRS], []))
    config = run.config_id

    steps = {
        "2 pairs + 1 duplicate": PAIRS + PAIRS[:1],
        "None": None,
        "1 new pair": [("UC1.txt", "Auth.java::Auth::clear()")],
        "empty list": [],
    }
    counts = {}
    for name, links in steps.items():
        service.record_element_links(db, config, links)
        db.commit()
        counts[name] = stored_pins(db, config)
    record(f"rows stored after each step: {counts}")

    assert counts == {"2 pairs + 1 duplicate": 2, "None": 2, "1 new pair": 1, "empty list": 0}


@case(
    id="G-08",
    feature="Configuration identity / get_or_create_config",
    level="graph",
    priority="Critical",
    why="A configuration row owns a graph. The same settings must find the same row, and a different pair of kinds must never share one.",
    input="Save three runs with identical settings: requirements->code twice, then architecture_document->code",
    expected="The first two share one configuration; the third gets its own; the project has 2 configurations with their kinds stored",
)
def test_same_settings_share_a_configuration_but_different_kinds_do_not(db, record):
    """Runs are filed under one configuration per combination of settings and kinds."""
    project = make_project(db)
    result = result_of(["UC1.txt"], ["Auth.java::Auth::login()"], [])

    first = save_run(db, project, result)
    again = save_run(db, project, result)
    documents = save_run(db, project, result, kinds=("architecture_document", "code"))

    configs = service.list_configs(db, project)
    record(f"config ids: first {first.config_id}, again {again.config_id}, documents {documents.config_id}")
    record(f"stored: {[(c.source_kind, c.target_kind, c.config_key) for c in configs]}")

    assert first.config_id == again.config_id != documents.config_id
    assert [(c.source_kind, c.target_kind) for c in configs] == [
        ("requirements", "code"), ("architecture_document", "code"),
    ]
    assert not any(c.is_default for c in configs)


@case(
    id="G-07",
    feature="Link pinning / pinned_links",
    level="graph",
    priority="High",
    why="The pipeline looks pins up by source identifier. The stored rows must come back grouped that way, per configuration.",
    preconditions="A run whose classifier judged UC1 linked to login and to logout",
    input="pinned_links() for that run's configuration, and for a second project's configuration",
    expected="{'UC1.txt': {login, logout}} for the first; an empty map for the other",
)
def test_pins_are_read_back_grouped_by_source(db, record):
    """A configuration's stored pairs come back as source -> set of targets, and only its own."""
    targets = [t for _, t in PAIRS]
    run = save_run(db, make_project(db), result_of(
        ["UC1.txt"], targets, [("UC1.txt", targets[0], 0.9), ("UC1.txt", targets[1], 0.8)],
    ))
    other = save_run(db, make_project(db), result_of(["UC1.txt"], targets, []))

    pins = service.pinned_links(db, run.config_id)
    record(f"pins: { {source: sorted(found) for source, found in pins.items()} }")
    record(f"another project's configuration: {service.pinned_links(db, other.config_id)}")

    assert pins == {"UC1.txt": set(targets)}
    assert service.pinned_links(db, other.config_id) == {}


@case(
    id="G-09",
    feature="Configuration identity / save_analysis",
    level="graph",
    priority="High",
    why="A run against a model does no dependency expansion. Storing depth 1 for it would claim work that never happened and split its configuration in two.",
    input="Save the same settings (depth 1) for requirements->code and for requirements->architecture",
    expected="Stored depth is 1 for the code target and 0 for the architecture target, on both the analysis and its configuration",
)
def test_expansion_depth_is_stored_as_zero_for_a_non_code_target(db, record):
    """The depth that is saved is the depth that was actually used."""
    project = make_project(db)
    result = result_of(["UC1.txt"], ["x"], [])

    code = save_run(db, project, result)
    model = save_run(db, project, result, kinds=("requirements", "architecture"))
    depths = {
        "code": (code.dependency_expansion_depth, db.get(ProjectConfig, code.config_id).dependency_expansion_depth),
        "architecture": (model.dependency_expansion_depth, db.get(ProjectConfig, model.config_id).dependency_expansion_depth),
    }
    record(f"(analysis depth, configuration depth): {depths}")

    assert CONFIG.dependency_expansion_depth == 1
    assert depths == {"code": (1, 1), "architecture": (0, 0)}


@case(
    id="G-10",
    feature="Versioning / next_version",
    level="graph",
    priority="High",
    why="Version numbers are how users line up runs with the state of their artifacts. They must count up per project, not globally.",
    input="Three versions in project A, then one in project B, then a fourth in A",
    expected="A: 1, 2, 3, 4. B: 1",
)
def test_versions_are_numbered_per_project(db, record):
    """Each project counts its own versions from 1."""
    a, b = make_project(db), make_project(db)

    numbers_a = [service.next_version(db, a).version_number for _ in range(3)]
    number_b = service.next_version(db, b).version_number
    numbers_a.append(service.next_version(db, a).version_number)
    db.commit()
    record(f"project A: {numbers_a}; project B: [{number_b}]")

    assert numbers_a == [1, 2, 3, 4] and number_b == 1
    assert [version.version_number for version, _ in service.list_versions(db, a)] == [4, 3, 2, 1]
