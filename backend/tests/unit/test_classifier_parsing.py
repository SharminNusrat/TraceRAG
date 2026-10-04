"""Reading a verdict out of what the chat model replied."""

from core.classification import ReasoningClassifier, SimpleClassifier
from tests.fakes import FakeChatProvider
from tests.recorder import case


def classifiers():
    # A provider is given so neither classifier builds its default Ollama one.
    return {
        "simple": SimpleClassifier(provider=FakeChatProvider()),
        "reasoning": ReasoningClassifier(provider=FakeChatProvider()),
    }


def linked(classifier, reply: str) -> bool:
    verdict = classifier._parse_response(reply)
    # The reasoning classifier answers (linked, explanation); the simple one just linked.
    return verdict[0] if isinstance(verdict, tuple) else verdict


@case(
    id="U-20",
    feature="Classification / response parsing",
    level="unit",
    priority="Medium",
    why="Every trace link is a parsed model reply. A reply read the wrong way is a wrong link shown to the user.",
    input="Well-formed replies: <trace>yes</trace>, <trace>NO</trace>, a <think> block saying yes followed by <trace>no</trace>, and a reply with an <explanation>",
    expected="yes -> linked, NO -> not linked, the <think> block is ignored, and the explanation text is returned",
)
def test_tagged_replies_are_read_correctly(record):
    """A reply that follows the prompt's format is read from its tags, whatever else it says."""
    replies = {
        "<trace>yes</trace>": True,
        "<trace>NO</trace>": False,
        "<think>yes, this could match</think>\n<trace>no</trace>": False,
        "Reasoning: yes and no.\n<trace>no</trace>": False,
    }
    for name, classifier in classifiers().items():
        seen = {reply: linked(classifier, reply) for reply in replies}
        record(f"{name}: {seen}")
        assert seen == replies, name

    verdict = classifiers()["reasoning"]._parse_response(
        "<trace>yes</trace><explanation> The method checks the passphrase. </explanation>"
    )
    record(f"reasoning with explanation: {verdict}")
    assert verdict == (True, "The method checks the passphrase.")


@case(
    id="U-19",
    feature="Classification / response parsing",
    level="unit",
    priority="Medium",
    why="Models sometimes omit the tag. Reading 'yes' inside another word turns a clear 'no' into a false trace link.",
    input="Replies with no <trace> tag: 'No. The eyes of the reader are not involved.' and 'No - that was removed yesterday.'",
    expected="Both read as not linked, by both classifiers",
)
def test_untagged_no_is_not_read_as_yes(record):
    """A reply without the tag that plainly says no must not become a link."""
    replies = [
        "No. The eyes of the reader are not involved.",
        "No - that was removed yesterday.",
    ]
    wrong = []
    for name, classifier in classifiers().items():
        for reply in replies:
            verdict = linked(classifier, reply)
            record(f"{name}: {reply!r} -> {'linked' if verdict else 'not linked'}")
            if verdict:
                wrong.append((name, reply))

    assert not wrong, f"{len(wrong)} of 4 replies saying 'No' were read as a link"
