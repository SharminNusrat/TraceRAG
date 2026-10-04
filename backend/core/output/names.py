"""What an element is called where a person reads it.

Most identifiers already read well enough - a file path, a method, a sentence's
place. A UML component's does not: it is the model file, a counter and an XMI id
(`bbb.uml$9$_0e5u8Fk...`). So a component is shown by the name the model gives
it, and everything else by its identifier. The identifier stays what is stored,
compared and tracked; this is only ever a label.
"""

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
    """Each element's display name, from (identifier, name) pairs of one side.

    An element with a name is shown by it. Two with the same name - the same
    component in two models - get their file added to tell them apart. One with
    no name is shown by its identifier.
    """
    taken = Counter(name for _, name in elements if name)
    return {
        identifier: (
            identifier if not name
            else name if taken[name] == 1
            else f"{name} ({file_of(identifier)})"
        )
        for identifier, name in elements
    }
