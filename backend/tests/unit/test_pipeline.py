"""The pipeline itself, run over real files with the fake models."""

import logging

from core.content import relative_identifier
from core.dependency import CodeDependencyAnalyzer
from tests.helpers import EXPECTED_LINKS, build_pipeline, copy_corpus, link_pairs, short
from tests.recorder import case


def pins_from(matrix, roots) -> dict[str, set[str]]:
    """A run's links in the form the application stores them for the next run."""
    pins: dict[str, set[str]] = {}
    for source, target in matrix.element_links:
        pins.setdefault(relative_identifier(source, roots), set()).add(
            relative_identifier(target, roots)
        )
    return pins


def two_method_requirement(source):
    """One requirement that names two methods, so a top-1 retrieval must miss one."""
    for file in source.iterdir():
        file.unlink()
    (source / "UC9.txt").write_text("A visitor can login and later logout.", encoding="utf-8")


@case(
    id="U-27",
    feature="Pipeline / link pinning",
    level="unit",
    priority="Critical",
    why="Top-k is a fixed budget. Without pinning, a link vanishes when retrieval stops surfacing it, though nothing rejected it.",
    preconditions="A first run with top-k 10 found both links of one requirement",
    input="The same files run again with top-k 1, once without pins and once with the first run's links as pins",
    expected="Without pins: 1 link (one lost). With pins: both links kept",
)
def test_pinned_links_survive_a_smaller_top_k(tmp_path, record):
    """A link retrieval no longer surfaces is offered to the classifier again and kept."""
    source, target = copy_corpus(tmp_path)
    two_method_requirement(source)
    roots = [source, target]

    first = build_pipeline(source, target, tmp_path / "chroma-1", n_results=10).run()
    record(f"first run, top-k 10: {sorted(link_pairs(first.trace_links))}")
    assert len(first.trace_links) == 2

    unpinned = build_pipeline(source, target, tmp_path / "chroma-2", n_results=1).run()
    record(f"top-k 1, no pins: {sorted(link_pairs(unpinned.trace_links))}")

    pinned = build_pipeline(
        source, target, tmp_path / "chroma-3", n_results=1, pinned_links=pins_from(first, roots),
    ).run()
    record(f"top-k 1, pinned: {sorted(link_pairs(pinned.trace_links))}")

    assert len(unpinned.trace_links) == 1
    assert link_pairs(pinned.trace_links) == link_pairs(first.trace_links)
    assert len(pinned.element_links) == 2


@case(
    id="U-26",
    feature="Pipeline / end to end",
    level="unit",
    priority="High",
    why="Checks load, split, embed, retrieve, classify and aggregate fit together before any database or API is involved.",
    input="tests/data: 4 use cases against 2 Java files (5 methods), whole documents -> methods, top-k 10",
    expected="UC1->login, UC2->logout, UC3->borrow; UC4 unimplemented; with expansion depth 1, a strong link to logout() also reaches clear(), which it calls",
)
def test_pipeline_recovers_the_expected_links(tmp_path, record):
    """The whole pipeline, on a corpus where the right answer is known."""
    source, target = copy_corpus(tmp_path)

    matrix = build_pipeline(source, target, tmp_path / "chroma").run()
    record(f"links: {sorted(link_pairs(matrix.trace_links))}")
    record(f"unimplemented: {[short(e.identifier) for e in matrix.get_unimplemented()]}")

    assert link_pairs(matrix.trace_links) == EXPECTED_LINKS
    assert [short(e.identifier) for e in matrix.get_unimplemented()] == ["UC4.txt"]

    # An expanded link is only kept when the link it grows from is a strong
    # one, so this requirement quotes the method it is about.
    (source / "UC5.txt").write_text("Logout. Signature: public void logout", encoding="utf-8")
    expanded = build_pipeline(
        source, target, tmp_path / "chroma-expanded",
        dependency_analyzer=CodeDependencyAnalyzer(), dependency_expansion_depth=1,
    ).run()
    from_uc5 = {target for origin, target in link_pairs(expanded.trace_links) if origin == "UC5.txt"}
    record(f"UC5 with dependency expansion: {sorted(from_uc5)}")

    assert from_uc5 == {"Auth.java::Auth::logout", "Auth.java::Auth::clear"}
    # Expanded links are derived, so they are not among the pairs kept for pinning.
    assert ("UC5.txt", "Auth.java::Auth::clear") not in {
        (short(origin), short(target)) for origin, target in expanded.element_links
    }


@case(
    id="U-28",
    feature="Pipeline / link pinning",
    level="unit",
    priority="Medium",
    why="Pins in the wrong form match nothing and look exactly like having nothing to carry over; the warning is the only sign.",
    preconditions="A first run with top-k 10 found both links of one requirement",
    input="Top-k 1 with pins keyed by absolute paths instead of the stored relative form",
    expected="Nothing carried over (1 link), and a warning that no pinned identifier matched",
)
def test_pins_in_the_wrong_form_carry_nothing_and_say_so(tmp_path, record, caplog):
    """Pins that do not match this run's identifiers are reported, not silently ignored."""
    source, target = copy_corpus(tmp_path)
    two_method_requirement(source)

    first = build_pipeline(source, target, tmp_path / "chroma-1", n_results=10).run()
    absolute: dict[str, set[str]] = {}
    for source_id, target_id in first.element_links:
        absolute.setdefault(str(source / source_id), set()).add(target_id)

    with caplog.at_level(logging.WARNING, logger="core.pipeline"):
        result = build_pipeline(
            source, target, tmp_path / "chroma-2", n_results=1, pinned_links=absolute,
        ).run()

    warnings = [r.getMessage() for r in caplog.records if "pinned source identifier" in r.getMessage()]
    record(f"links: {len(result.trace_links)}")
    record(f"warning: {warnings[0] if warnings else 'none logged'}")

    assert len(result.trace_links) == 1
    assert warnings
