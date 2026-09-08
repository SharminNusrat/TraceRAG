"""Identity for one way of reading a project's artifacts.

Two runs can only be compared if they read the artifacts the same way. A
file-level run and a sentence-level run produce different elements from the
same file, so diffing them would report every element as new and every link as
both added and removed.

That makes the configuration - not the project - the thing an element set, a
vector collection and a comparison all belong to. This is its name.
"""

from enum import Enum
from hashlib import sha256

# Long enough that two configurations will not collide, short enough that the
# key can name a vector collection - those have a 63-character ceiling.
KEY_LENGTH = 16


def _text(value) -> str:
    """A setting as it is stored, not as its type prints it."""
    # PreprocessorType.SECTION formats as "PreprocessorType.SECTION" but is
    # stored as "section". One caller holds the enum and another the string, so
    # without this the same configuration would key two different ways.
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def config_key(
    *,
    source_preprocessor: str,
    target_preprocessor: str,
    source_output_level: str | None,
    target_output_level: str | None,
    classifier: str,
    n_results: int,
    dependency_expansion_depth: int,
    summarize_elements: bool,
) -> str:
    """A stable short name for this configuration.

    Every setting that changes what a run finds, and nothing else: the
    preprocessors decide what the elements are, the output levels decide what
    the links are reported on, and the rest decide which of them survive.
    Anything not named here may differ between two runs without making them
    incomparable.

    Keyword-only and spelled out rather than taking a settings object: the
    request, the stored config and the saved analysis each name these fields
    slightly differently, and the key must not change with the caller.
    """
    # Written as text, so None and False have one spelling each and the key
    # does not depend on which type the caller happened to pass.
    parts = (
        f"source_preprocessor={_text(source_preprocessor)}",
        f"target_preprocessor={_text(target_preprocessor)}",
        f"source_output_level={_text(source_output_level)}",
        f"target_output_level={_text(target_output_level)}",
        f"classifier={_text(classifier)}",
        f"n_results={_text(n_results)}",
        f"dependency_expansion_depth={_text(dependency_expansion_depth)}",
        f"summarize_elements={_text(summarize_elements)}",
    )
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:KEY_LENGTH]
