"""What an element is called where a person reads it."""

from collections import Counter

# The output level whose elements have a name of their own.
LEVEL_COMPONENT = "component"


def model_name(level: str, model_units) -> str | None:
    """A component's name from its model, or None for any other element."""
    if level != LEVEL_COMPONENT or model_units is None:
        return None
    name = model_units.get("name") if isinstance(model_units, dict) else model_units.name
    return name or None


def file_of(identifier: str) -> str:
    """The file an element lives in: its identifier up to "::" or "$"."""
    return identifier.partition("::")[0].partition("$")[0]


def display_names(elements: list[tuple[str, str | None]]) -> dict[str, str]:
    """Each element's display name, from (identifier, name) pairs of one side."""
    taken = Counter(name for _, name in elements if name)
    return {
        identifier: (
            identifier if not name
            else name if taken[name] == 1
            else f"{name} ({file_of(identifier)})"
        )
        for identifier, name in elements
    }
