"""What each test is for, and what it saw when it ran.

The test report is written from this and nothing else: the description comes
from `case`, the actual output from `record`, and the verdict from pytest.
"""

import pytest

LEVELS = ("unit", "graph", "integration")
PRIORITIES = ("Critical", "High", "Medium")


def case(
    id: str,
    feature: str,
    level: str,
    priority: str,
    why: str,
    input: str,
    expected: str,
    preconditions: str = "None",
):
    """Describe a test case. The test's docstring is its description."""
    assert level in LEVELS and priority in PRIORITIES, (level, priority)

    def mark(function):
        function.case = {
            "id": id,
            "feature": feature,
            "level": level,
            "priority": priority,
            "why": why,
            "preconditions": preconditions,
            "input": input,
            "expected": expected,
        }
        # Also a pytest marker, so one level can be run on its own: -m unit
        return getattr(pytest.mark, level)(function)

    return mark
