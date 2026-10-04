"""The name a configuration is known by, and the settings that go into it."""

from api.schemas import ClassifierType, PreprocessorType
from core.projects.run_config import KEY_LENGTH, config_key, expansion_depth
from core.schemas import ElementLevel
from tests.recorder import case

SETTINGS = dict(
    source_kind="requirements",
    target_kind="code",
    source_preprocessor="single",
    target_preprocessor="method",
    source_output_level="artifact",
    target_output_level="file",
    classifier="reasoning",
    n_results=10,
    dependency_expansion_depth=1,
    summarize_elements=True,
)


@case(
    id="U-02",
    feature="Configuration identity / config_key",
    level="unit",
    priority="Critical",
    why="Two relations sharing one key share one graph: their links would be merged and overwrite each other silently.",
    input="Identical settings, once for requirements -> code and once for architecture_document -> code",
    expected="Two different keys",
)
def test_different_kinds_get_different_keys(record):
    """Requirements and architecture documents use the same preprocessors, so only the kinds tell them apart."""
    requirements = config_key(**SETTINGS)
    documents = config_key(**{**SETTINGS, "source_kind": "architecture_document"})
    swapped = config_key(**{**SETTINGS, "source_kind": "code", "target_kind": "requirements"})
    record(f"requirements -> code: {requirements}")
    record(f"architecture_document -> code: {documents}")
    record(f"code -> requirements: {swapped}")

    assert len({requirements, documents, swapped}) == 3


@case(
    id="U-01",
    feature="Configuration identity / config_key",
    level="unit",
    priority="High",
    why="The request holds enums and the database holds strings; if they keyed differently, every re-run would start a new graph.",
    input="The same settings passed as enum members and as their stored string values",
    expected=f"The same {KEY_LENGTH}-character key both times",
)
def test_same_settings_give_the_same_key(record):
    """A key does not depend on whether the caller held an enum or its stored text."""
    as_text = config_key(**SETTINGS)
    as_enums = config_key(**{
        **SETTINGS,
        "source_preprocessor": PreprocessorType.SINGLE,
        "target_preprocessor": PreprocessorType.METHOD,
        "source_output_level": ElementLevel.ARTIFACT,
        "target_output_level": ElementLevel.FILE,
        "classifier": ClassifierType.REASONING,
    })
    record(f"from strings: {as_text}; from enums: {as_enums}")

    assert as_text == as_enums == config_key(**SETTINGS)
    assert len(as_text) == KEY_LENGTH


@case(
    id="U-03",
    feature="Configuration identity / config_key",
    level="unit",
    priority="Medium",
    why="A setting left out of the key would let two runs that find different things be compared as if they were the same.",
    input="The base settings, then each setting changed one at a time",
    expected="Every change produces a key different from the base and from each other",
)
def test_every_setting_changes_the_key(record):
    """Each of the ten settings is part of the key."""
    changes = {
        "source_kind": "architecture_document",
        "target_kind": "architecture",
        "source_preprocessor": "sentence",
        "target_preprocessor": "line",
        "source_output_level": "sentence",
        "target_output_level": "function",
        "classifier": "simple",
        "n_results": 12,
        "dependency_expansion_depth": 2,
        "summarize_elements": False,
    }
    keys = {name: config_key(**{**SETTINGS, name: value}) for name, value in changes.items()}
    record(f"{len(set(keys.values()))} distinct keys from {len(changes)} single-setting changes")

    assert config_key(**SETTINGS) not in keys.values()
    assert len(set(keys.values())) == len(changes)


@case(
    id="U-04",
    feature="Configuration identity / expansion_depth",
    level="unit",
    priority="High",
    why="Expansion only happens for code. Recording it for other kinds splits identical runs into two configurations.",
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
