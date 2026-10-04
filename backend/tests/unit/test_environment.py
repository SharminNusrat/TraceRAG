"""The test environment itself: is it really separate from the development one?"""

import socket
from pathlib import Path

import pytest

from config import settings
from core.db.session import engine
from core.projects import artifact_store
from core.schemas import Element, ElementLevel
from tests.conftest import TEST_DATABASE, WORK_DIR, development_database_url
from tests.fakes import FakeClassifier, FakeEmbedder
from tests.recorder import case


def element(identifier: str, content: str) -> Element:
    return Element(
        identifier=identifier, type="text", content=content,
        granularity=0, level=ElementLevel.ARTIFACT,
    )


@case(
    id="S-01",
    feature="Test environment / isolation",
    level="unit",
    priority="Critical",
    why="A test run that touched the development database or its stored files would destroy real work.",
    input="The settings the application loaded under pytest",
    expected=f"Database is '{TEST_DATABASE}', not the one in .env; storage and the working folder are under tests/.work",
)
def test_tests_use_their_own_database_and_storage(record):
    """The application under test is pointed at the test database and a scratch storage folder."""
    development = development_database_url().database
    record(f"database in use: {engine.url.database}; database in .env: {development}")
    record(f"storage root: {artifact_store.STORAGE_ROOT}")
    record(f"working folder (where ./chroma_data is written): {Path.cwd()}")

    assert engine.url.database == TEST_DATABASE
    assert engine.url.database != development
    assert artifact_store.STORAGE_ROOT.is_relative_to(WORK_DIR)
    assert Path.cwd().is_relative_to(WORK_DIR)
    assert settings.groq_api_keys == "not-a-real-key"


@case(
    id="S-02",
    feature="Test environment / network guard",
    level="unit",
    priority="High",
    why="Guarantees no test can reach Ollama, Groq, GitHub or HuggingFace, so results never depend on them.",
    input="Open a socket to api.github.com:443 and to localhost:11434 (Ollama)",
    expected="Both refused with OSError before any connection is made",
)
def test_outside_connections_are_refused(record):
    """A connection that would leave the machine, or reach Ollama, is refused."""
    from tests import conftest

    for address in (("api.github.com", 443), ("localhost", 11434)):
        with pytest.raises(OSError) as refusal:
            socket.socket().connect(address)
        record(f"{address[0]}:{address[1]} -> {refusal.value}")

    # These two were this test's own doing, not a leak.
    conftest.blocked.clear()


@case(
    id="S-03",
    feature="Test environment / fake models",
    level="unit",
    priority="High",
    why="Every other test's result depends on the fakes giving the same answer on every run.",
    input="Embed the same text twice; classify a related pair and an unrelated pair",
    expected="Identical vectors; related pair linked, unrelated pair not; shared-word texts closer than unrelated ones",
)
def test_fakes_are_deterministic(record):
    """The fake embedder and classifier give the same answer every time."""
    embedder = FakeEmbedder()
    login = element("req", "The member can login with a password.")
    code = element("code", "boolean login(String name) { return check(name); }")
    other = element("other", "void printReport() { render(); }")

    first, second = embedder.create_embedding(login), embedder.create_embedding(login)
    assert first == second

    def closeness(a, b):
        return sum(x * y for x, y in zip(embedder.create_embedding(a), embedder.create_embedding(b)))

    record(f"similarity(login req, login code) = {closeness(login, code):.3f}")
    record(f"similarity(login req, report code) = {closeness(login, other):.3f}")
    assert closeness(login, code) > closeness(login, other)

    classifier = FakeClassifier()
    results = classifier.classify(login, [(code, 0.9), (other, 0.1)])
    record(f"linked targets: {[result.target.identifier for result in results]}")
    record(f"explanation: {results[0].explanation}")
    assert [result.target.identifier for result in results] == ["code"]
    assert len(classifier.asked) == 2
