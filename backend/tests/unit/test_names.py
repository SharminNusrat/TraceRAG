"""What an element is called where a person reads it."""

from core.output.names import display_names, model_name
from tests.recorder import case


@case(
    id="U-57",
    feature="Display names / display_names",
    level="unit",
    priority="High",
    why="A UML component's identifier is a counter and an XMI id, which means nothing to a reader. Every screen and export shows the name this returns, so it has to name each element once and unambiguously.",
    input="A component 'Authentication'; two components called 'Lending' in two model files; a component with no name; a sentence and a method",
    expected="'Authentication'; 'Lending (a.uml)' and 'Lending (b.uml)'; the nameless component and the sentence and method keep their identifiers. model_name gives a name only at component level",
)
def test_components_are_shown_by_name(record):
    """Components are named by their model; duplicates get their file; everything else keeps its id."""
    elements = [
        ("bbb.uml$0$_auth", "Authentication"),
        ("a.uml$1$_lend", "Lending"),
        ("b.uml$1$_lend", "Lending"),
        ("bbb.uml$2$_x", None),
        ("UC1.txt::sentence_8", None),
        ("Auth.java::Auth::login()", None),
    ]
    names = display_names(elements)
    record(names)

    assert names == {
        "bbb.uml$0$_auth": "Authentication",
        "a.uml$1$_lend": "Lending (a.uml)",
        "b.uml$1$_lend": "Lending (b.uml)",
        "bbb.uml$2$_x": "bbb.uml$2$_x",
        "UC1.txt::sentence_8": "UC1.txt::sentence_8",
        "Auth.java::Auth::login()": "Auth.java::Auth::login()",
    }
    assert model_name("component", {"name": "Authentication"}) == "Authentication"
    assert model_name("interface", {"name": "IAuth"}) is None
    assert model_name("artifact", None) is None
