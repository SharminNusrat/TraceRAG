"""The change report between two versions of one analysis: every link's state, and why.

A pure function over two runs and the id maps between them, so each state and
each reason is checked here on its own, with nothing else in the way.
"""

from core.projects.diff import IdMap, element_hash
from core.projects.report import RunView, change_report, count_file_links
from tests.recorder import case

LOGIN, LOGOUT, BORROW = "Auth::login()", "Auth::logout()", "Loans::borrow()"


def run(links, sources: dict[str, str], targets: dict[str, str], uncovered=()) -> RunView:
    """One run: its links as (source, target), and each element's text."""
    return RunView(
        links={pair: {"confidence": 0.9, "confidence_level": "high", "explanation": None} for pair in links},
        source_hashes={name: element_hash(text) for name, text in sources.items()},
        target_hashes={name: element_hash(text) for name, text in targets.items()},
        uncovered=list(uncovered),
    )


REQUIREMENTS = {"UC1": "login with a passphrase", "UC2": "logout at any time", "UC3": "borrow a title"}
CODE = {LOGIN: "boolean login()", LOGOUT: "void logout()", BORROW: "void borrow()"}
ALL = [("UC1", LOGIN), ("UC2", LOGOUT), ("UC3", BORROW)]


def states(report) -> dict:
    return {(link["source_id"], link["target_id"]): link["state"] for link in report["links"]}


def reasons(report) -> dict:
    return {
        (link["source_id"], link["target_id"]): (link["source_changed"], link["target_changed"])
        for link in report["links"]
    }


@case(
    id="U-39",
    feature="Change report / valid",
    level="unit",
    priority="Critical",
    why="A link found in both versions is the normal case. If it is not reported valid, every update looks like a loss.",
    input="The same 3 links in both versions, over the same elements",
    expected="3 valid links, none changed at either end; summary valid=3 and every other count 0",
)
def test_link_in_both_versions_is_valid(record):
    """A link both runs found, between unchanged elements, is valid and neither end changed."""
    report = change_report(run(ALL, REQUIREMENTS, CODE), run(ALL, REQUIREMENTS, CODE), IdMap(), IdMap())
    record(report["summary"])

    assert set(states(report).values()) == {"valid"}
    assert set(reasons(report).values()) == {(False, False)}
    assert report["summary"] == {"valid": 3, "no_longer_found": 0, "broken": 0, "new": 0, "uncovered": 0}


@case(
    id="U-40",
    feature="Change report / no longer found, neither end changed",
    level="unit",
    priority="Critical",
    why="When neither element changed and the link is gone, the cause is the classifier or a shifted top-k - not the artifacts. The report has to say so, because that is the case pinning exists for.",
    input="UC2 -> logout in the earlier version only; both elements unchanged and still present",
    expected="UC2 -> logout: no_longer_found, source_changed=false, target_changed=false",
)
def test_link_lost_with_neither_end_changed(record):
    """A link that disappeared between two unchanged elements is no longer found, for no reason in the files."""
    later = [("UC1", LOGIN), ("UC3", BORROW)]
    report = change_report(run(ALL, REQUIREMENTS, CODE), run(later, REQUIREMENTS, CODE), IdMap(), IdMap())
    lost = next(link for link in report["links"] if link["source_id"] == "UC2")
    record(lost)

    assert lost["state"] == "no_longer_found"
    assert (lost["source_changed"], lost["target_changed"]) == (False, False)
    assert lost["source_present"] and lost["target_present"]


@case(
    id="U-41",
    feature="Change report / source changed",
    level="unit",
    priority="Critical",
    why="An edited requirement is the most common reason a link moves. The report must name the source as the reason.",
    input="UC2's text edited between the versions, and UC2 -> logout no longer found",
    expected="UC2 -> logout: no_longer_found, source_changed=true, target_changed=false",
)
def test_link_lost_because_the_source_changed(record):
    """A link lost after its requirement was edited names the source as what changed."""
    edited = {**REQUIREMENTS, "UC2": "sign out is no longer offered"}
    later = [("UC1", LOGIN), ("UC3", BORROW)]
    report = change_report(run(ALL, REQUIREMENTS, CODE), run(later, edited, CODE), IdMap(), IdMap())
    record(reasons(report))

    assert states(report)[("UC2", LOGOUT)] == "no_longer_found"
    assert reasons(report)[("UC2", LOGOUT)] == (True, False)


@case(
    id="U-42",
    feature="Change report / target changed",
    level="unit",
    priority="Critical",
    why="Code changes under a requirement all the time. When that is what moved a link, the report must point at the code.",
    input="logout()'s body edited between the versions; UC2 -> logout still found",
    expected="UC2 -> logout: valid, source_changed=false, target_changed=true; the other two links valid with neither changed",
)
def test_link_kept_although_the_target_changed(record):
    """A link kept across an edit to its code says the target changed."""
    edited = {**CODE, LOGOUT: "void logout() { session.end(); }"}
    report = change_report(run(ALL, REQUIREMENTS, CODE), run(ALL, REQUIREMENTS, edited), IdMap(), IdMap())
    record(reasons(report))

    assert states(report)[("UC2", LOGOUT)] == "valid"
    assert reasons(report)[("UC2", LOGOUT)] == (False, True)
    assert reasons(report)[("UC1", LOGIN)] == (False, False)


@case(
    id="U-43",
    feature="Change report / broken",
    level="unit",
    priority="Critical",
    why="A link whose requirement or code was removed is broken: the one state that always needs a person to look. It must not be mistaken for the classifier changing its mind.",
    input="UC3 removed from the requirements (its id map entry says removed), and borrow() removed from the code in a second case",
    expected="UC3 -> borrow: broken with source_present=false and source_changed=true. In the second case: broken with target_present=false and target_changed=true",
)
def test_link_to_a_removed_element_is_broken(record):
    """A link with an end that no longer exists is broken, and says which end."""
    without_uc3 = {name: text for name, text in REQUIREMENTS.items() if name != "UC3"}
    without_borrow = {name: text for name, text in CODE.items() if name != BORROW}
    later = [("UC1", LOGIN), ("UC2", LOGOUT)]

    source_gone = change_report(run(ALL, REQUIREMENTS, CODE), run(later, without_uc3, CODE),
                                IdMap(removed={"UC3"}), IdMap())
    target_gone = change_report(run(ALL, REQUIREMENTS, CODE), run(later, REQUIREMENTS, without_borrow),
                                IdMap(), IdMap(removed={BORROW}))
    first = next(link for link in source_gone["links"] if link["state"] == "broken")
    second = next(link for link in target_gone["links"] if link["state"] == "broken")
    record(f"requirement removed: {first}")
    record(f"code removed: {second}")

    assert (first["source_id"], first["source_present"], first["source_changed"]) == ("UC3", False, True)
    assert (second["target_id"], second["target_present"], second["target_changed"]) == (BORROW, False, True)
    assert source_gone["summary"]["broken"] == target_gone["summary"]["broken"] == 1


@case(
    id="U-44",
    feature="Change report / new",
    level="unit",
    priority="High",
    why="A link only the later run found is what an update adds. Whether it came from new text or from the classifier matters for trusting it.",
    input="A new requirement UC4 linked to borrow() in the later version, and a new link UC1 -> logout between two unchanged elements",
    expected="UC4 -> borrow: new, source_changed=true (UC4 did not exist). UC1 -> logout: new, neither changed",
)
def test_link_only_in_the_later_version_is_new(record):
    """A link only the later run found is new, and says whether either end is new or edited."""
    grown = {**REQUIREMENTS, "UC4": "borrow a second title"}
    later = ALL + [("UC4", BORROW), ("UC1", LOGOUT)]
    report = change_report(run(ALL, REQUIREMENTS, CODE), run(later, grown, CODE), IdMap(), IdMap())
    record(reasons(report))

    assert states(report)[("UC4", BORROW)] == "new" and reasons(report)[("UC4", BORROW)] == (True, False)
    assert states(report)[("UC1", LOGOUT)] == "new" and reasons(report)[("UC1", LOGOUT)] == (False, False)
    assert report["summary"]["new"] == 2


@case(
    id="U-45",
    feature="Change report / uncovered",
    level="unit",
    priority="High",
    why="Requirements with no link are the coverage gap a report exists to show. A requirement that was deleted is not a gap, and must not be listed as one.",
    input="Later version: UC2 has no link and still exists; UC3 was removed. The later run lists UC2 as unimplemented",
    expected="uncovered = ['UC2'] (UC3 is not listed); summary uncovered=1",
)
def test_uncovered_sources_are_listed_and_deleted_ones_are_not(record):
    """Sources with no link in the later version are listed; removed ones are not."""
    later = {name: text for name, text in REQUIREMENTS.items() if name != "UC3"}
    report = change_report(
        run(ALL, REQUIREMENTS, CODE),
        run([("UC1", LOGIN)], later, CODE, uncovered=["UC2"]),
        IdMap(removed={"UC3"}), IdMap(),
    )
    record(f"uncovered: {report['uncovered']}; summary {report['summary']}")

    assert report["uncovered"] == ["UC2"]
    assert report["summary"]["uncovered"] == 1


@case(
    id="U-46",
    feature="Change report / identifiers that moved",
    level="unit",
    priority="Critical",
    why="With sentence-level requirements, inserting a sentence renames the ones after it. Read without the id map, every later link would show as no longer found plus new.",
    input="Earlier: s0 -> login, s1 -> logout. A sentence inserted at 1, so s1 is now s2 (id map). Later: s0 -> login, s2 -> logout",
    expected="Both links valid, reported under the later identifiers (s0, s2), with neither end changed; no link new or no longer found",
)
def test_links_are_matched_through_the_id_map(record):
    """A link whose element moved is the same link under its new identifier."""
    before = run([("s0", LOGIN), ("s1", LOGOUT)], {"s0": "login", "s1": "logout"}, CODE)
    after = run([("s0", LOGIN), ("s2", LOGOUT)], {"s0": "login", "s1": "lock accounts", "s2": "logout"}, CODE)
    report = change_report(before, after, IdMap(moved={"s1": "s2"}), IdMap())
    record(states(report))

    assert states(report) == {("s0", LOGIN): "valid", ("s2", LOGOUT): "valid"}
    assert set(reasons(report).values()) == {(False, False)}


@case(
    id="U-47",
    feature="Change report / the first version",
    level="unit",
    priority="Medium",
    why="An analysis that was never updated has nothing to compare with. Its links must still be listed, as they stand.",
    input="No earlier run; a later run with 3 links and UC4 uncovered",
    expected="3 links, all valid with neither end changed; uncovered ['UC4']",
)
def test_a_first_version_lists_its_links_as_they_stand(record):
    """With nothing to compare against, every link is reported as it stands."""
    sources = {**REQUIREMENTS, "UC4": "print a monthly overview"}
    report = change_report(None, run(ALL, sources, CODE, uncovered=["UC4"]), IdMap(), IdMap())
    record(report["summary"])

    assert set(states(report).values()) == {"valid"}
    assert set(reasons(report).values()) == {(False, False)}
    assert report["uncovered"] == ["UC4"]



@case(
    id="U-56",
    feature="Change report / links per changed file",
    level="unit",
    priority="High",
    why="A changed file is worth looking at in proportion to the links it touches. Counting them per file is what lets the user go from a change to the links it affected.",
    input="Changed code files: Auth.java (modified) and security/Pay.java (renamed from Pay.java). Links: two into Auth.java (one whose target changed), one into security/Pay.java, one into Loans.java",
    expected="Auth.java: 2 links, 1 changed. security/Pay.java: 1 link, 1 changed. Loans.java is not a changed file and is not counted",
)
def test_links_are_counted_per_changed_file(record):
    """Each changed file says how many links touch it on its side, and how many say that end changed."""
    rows = [
        {"path": "Auth.java", "old_path": None},
        {"path": "security/Pay.java", "old_path": "Pay.java"},
    ]
    links = [
        {"source_id": "UC1", "target_id": "Auth.java::Auth::login()", "source_changed": False, "target_changed": True},
        {"source_id": "UC2", "target_id": "Auth.java::Auth::logout()", "source_changed": False, "target_changed": False},
        {"source_id": "UC3", "target_id": "security/Pay.java::Pay::charge()", "source_changed": True, "target_changed": True},
        {"source_id": "UC4", "target_id": "Loans.java::Loans::borrow()", "source_changed": False, "target_changed": True},
    ]
    count_file_links(rows, links, "target")
    record([(row["path"], row["links"], row["changed"]) for row in rows])

    assert [(row["links"], row["changed"]) for row in rows] == [(2, 1), (1, 1)]
