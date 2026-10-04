"""Analysis identity, versions, and the links kept for the next run to re-offer."""

from sqlalchemy import func, select

from core.db.models import ElementLink
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
    why="These rows are what the next update re-offers to the classifier. Lose them and links silently drop out; keep stale ones and dead pairs are proposed forever.",
    preconditions="An analysis with 2 stored pairs",
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
    feature="Analysis identity / create_config",
    level="graph",
    priority="Critical",
    why="An analysis owns its versions and its pins. If a New Analysis were filed under an earlier one with the same settings, a new upload would become a later version of an unrelated analysis.",
    input="Save three New Analyses in one project: requirements->code twice with identical settings, then architecture_document->code",
    expected="Three analyses, each at version 1 with its own runs; the project lists all three with their kinds",
)
def test_every_new_analysis_is_its_own_analysis(db, record):
    """Identical settings do not make two New Analyses one analysis."""
    project = make_project(db)
    result = result_of(["UC1.txt"], ["Auth.java::Auth::login()"], [])

    first = save_run(db, project, result)
    again = save_run(db, project, result)
    documents = save_run(db, project, result, kinds=("architecture_document", "code"))

    configs = service.list_configs(db, project)
    numbers = [run.version.version_number for run in (first, again, documents)]
    record(f"analysis ids: first {first.config_id}, again {again.config_id}, documents {documents.config_id}")
    record(f"version numbers: {numbers}")
    record(f"stored: {[(c.source_kind, c.target_kind) for c in configs]}")

    assert len({first.config_id, again.config_id, documents.config_id}) == 3
    assert numbers == [1, 1, 1]
    assert [(c.source_kind, c.target_kind) for c in configs] == [
        ("requirements", "code"), ("requirements", "code"), ("architecture_document", "code"),
    ]


@case(
    id="G-07",
    feature="Link pinning / pinned_links",
    level="graph",
    priority="High",
    why="The pipeline looks pins up by source identifier. The stored rows must come back grouped that way, per analysis.",
    preconditions="A run whose classifier judged UC1 linked to login and to logout",
    input="pinned_links() for that run's analysis, and for a second project's analysis",
    expected="{'UC1.txt': {login, logout}} for the first; an empty map for the other",
)
def test_pins_are_read_back_grouped_by_source(db, record):
    """An analysis's stored pairs come back as source -> set of targets, and only its own."""
    targets = [t for _, t in PAIRS]
    run = save_run(db, make_project(db), result_of(
        ["UC1.txt"], targets, [("UC1.txt", targets[0], 0.9), ("UC1.txt", targets[1], 0.8)],
    ))
    other = save_run(db, make_project(db), result_of(["UC1.txt"], targets, []))

    pins = service.pinned_links(db, run.config_id)
    record(f"pins: { {source: sorted(found) for source, found in pins.items()} }")
    record(f"another project's analysis: {service.pinned_links(db, other.config_id)}")

    assert pins == {"UC1.txt": set(targets)}
    assert service.pinned_links(db, other.config_id) == {}


@case(
    id="G-09",
    feature="Analysis identity / create_config",
    level="graph",
    priority="High",
    why="A run against a model does no dependency expansion. Storing depth 1 for it would claim work that never happened.",
    input="Save the same settings (depth 1) for requirements->code and for requirements->architecture",
    expected="The stored depth is 1 for the code target and 0 for the architecture target",
)
def test_expansion_depth_is_stored_as_zero_for_a_non_code_target(db, record):
    """The depth that is saved is the depth that was actually used."""
    project = make_project(db)
    result = result_of(["UC1.txt"], ["x"], [])

    code = save_run(db, project, result)
    model = save_run(db, project, result, kinds=("requirements", "architecture"))
    depths = {
        "code": code.config.dependency_expansion_depth,
        "architecture": model.config.dependency_expansion_depth,
    }
    record(f"stored depth per target: {depths}")

    assert CONFIG.dependency_expansion_depth == 1
    assert depths == {"code": 1, "architecture": 0}


@case(
    id="G-10",
    feature="Versioning / next_version",
    level="graph",
    priority="High",
    why="Version numbers are how users line up runs with the state of their files. They must count up per analysis - never shared with another analysis, even in the same project.",
    input="Three versions in analysis A, then one in analysis B of the same project, then a fourth in A",
    expected="A: 1, 2, 3, 4. B: 1",
)
def test_versions_are_numbered_per_analysis(db, record):
    """Each analysis counts its own versions from 1."""
    project = make_project(db)
    a = service.create_config(db, project, CONFIG, "requirements", "code")
    b = service.create_config(db, project, CONFIG, "requirements", "code")

    numbers_a = [service.next_version(db, a).version_number for _ in range(3)]
    number_b = service.next_version(db, b).version_number
    numbers_a.append(service.next_version(db, a).version_number)
    db.commit()
    record(f"analysis A: {numbers_a}; analysis B: [{number_b}]")

    assert numbers_a == [1, 2, 3, 4] and number_b == 1
    assert [version.version_number for version, _ in service.list_versions(db, a)] == [4, 3, 2, 1]
