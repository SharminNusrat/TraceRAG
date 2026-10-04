"""The settings a run actually uses."""

from core.projects.run_config import expansion_depth
from tests.recorder import case


@case(
    id="U-04",
    feature="Analysis settings / expansion_depth",
    level="unit",
    priority="High",
    why="Expansion only happens for code. Recording it for other kinds would claim an expansion that never happened.",
    input="Depth 2 with target kind code, architecture, architecture_document, requirements, and unknown (None)",
    expected="code -> 2; the other three kinds -> 0; unknown -> 2 (left as given)",
)
def test_expansion_only_counts_for_a_code_target(record):
    """Dependency expansion is recorded as 0 unless the target is code."""
    depths = {
        kind: expansion_depth(kind, 2)
        for kind in ("code", "architecture", "architecture_document", "requirements", None)
    }
    record(f"depth 2 becomes: {depths}")

    assert depths == {
        "code": 2, "architecture": 0, "architecture_document": 0, "requirements": 0, None: 2,
    }
