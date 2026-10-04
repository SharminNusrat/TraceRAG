"""Where the tests run: a database, a storage folder and a working folder of their own.

The application reads its settings - and opens its database - the moment it is
imported. So everything that decides *which* database and *which* folders
happens here first, at the top of this file, before any application import.
"""

import json
import os
import platform
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
WORK_DIR = TESTS_DIR / ".work"
RESULTS_FILE = TESTS_DIR / "results" / "results.json"

# Kept after the run, so what the tests left behind can be looked at. Emptied
# at the start of the next run rather than at the end of this one.
TEST_DATABASE = "tracerag_test"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
OLLAMA_PORT = 11434


def operating_system() -> str:
    """The OS as a reader would name it. Python reports Windows 11 as release '10'."""
    if platform.system() != "Windows":
        return platform.platform()
    build = platform.version()
    release = "11" if int(build.split(".")[-1]) >= 22000 else platform.release()
    return f"Windows {release} (build {build})"


def development_database_url():
    """The URL in backend/.env, which says where the server is and how to log in."""
    for line in (BACKEND_DIR / ".env").read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip().upper() == "DATABASE_URL":
            return make_url(value.strip().strip("\"'"))
    raise RuntimeError("backend/.env has no DATABASE_URL to take the server from.")


def create_test_database() -> str:
    """Make sure the test database exists, and return the URL that reaches it."""
    development = development_database_url()
    if development.database == TEST_DATABASE:
        # The tests empty every table before they start.
        raise RuntimeError(
            f"backend/.env points at '{TEST_DATABASE}', which is the database the "
            f"tests wipe. Refusing to run against it."
        )

    # CREATE DATABASE cannot run inside a transaction.
    server = create_engine(development.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DATABASE}
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    server.dispose()

    return development.set(database=TEST_DATABASE).render_as_string(hide_password=False)


# ----- Network: nothing but this machine -----

# Connections refused while the application was being imported, kept apart
# from the ones a test causes so they can be reported without failing it.
blocked_at_import: list[str] = []
blocked: list[str] = []
real_connect = socket.socket.connect


def guarded_connect(self, address):
    """Refuse any connection that would leave this machine, or reach Ollama."""
    if not isinstance(address, tuple):
        return real_connect(self, address)
    host, port = address[0], address[1]
    if host in LOCAL_HOSTS and port != OLLAMA_PORT:
        return real_connect(self, address)
    blocked.append(f"{host}:{port}")
    raise OSError(f"Network access is blocked in tests: {host}:{port}")


socket.socket.connect = guarded_connect


# ----- Environment: set before the application is imported -----

shutil.rmtree(WORK_DIR, ignore_errors=True)
(WORK_DIR / "storage").mkdir(parents=True)
(WORK_DIR / "cwd").mkdir(parents=True)

os.environ.update({
    "DATABASE_URL": create_test_database(),
    "STORAGE_PATH": str(WORK_DIR / "storage"),
    # Fixed, so tokens and encrypted values are the same on every run.
    "SECRET_KEY": "tracerag-test-secret-key-0123456789abcdef",
    "GITHUB_CLIENT_ID": "test-client-id",
    "GITHUB_CLIENT_SECRET": "test-client-secret",
    "GITHUB_TOKEN": "",
    "GROQ_API_KEYS": "not-a-real-key",
    "FRONTEND_URL": "http://localhost:5173",
    "ANONYMIZED_TELEMETRY": "False",
    "HF_HUB_OFFLINE": "1",
})
import core.db.models  # noqa: E402,F401  (registers every table)
from core.db.session import Base, SessionLocal, engine, init_db  # noqa: E402

init_db()
with engine.begin() as connection:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

import main  # noqa: E402
from api import pipeline_factory  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests import fakes  # noqa: E402

blocked_at_import.extend(blocked)
blocked.clear()


def pytest_sessionstart(session):
    # The vector indexes are written to ./chroma_data, relative to wherever the
    # process is standing. Standing here keeps them out of the real one. Done
    # now rather than at import, once pytest has finished reading its own paths.
    os.chdir(WORK_DIR / "cwd")


# ----- Fixtures -----

@pytest.fixture(autouse=True)
def no_network():
    """Fail a test that tried to reach anything outside this machine."""
    blocked.clear()
    yield
    assert not blocked, f"The test tried to reach the network: {blocked}"


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    """Swap the models the API would build for ones that need no network."""
    monkeypatch.setattr(pipeline_factory, "OllamaEmbeddingCreator", lambda **_: fakes.FakeEmbedder())
    monkeypatch.setattr(pipeline_factory, "get_classifier", fakes.classifier_for)
    monkeypatch.setattr(pipeline_factory, "get_chat_provider", fakes.FakeChatProvider)


@pytest.fixture
def github(monkeypatch):
    """A pretend GitHub, in place of the real one everywhere the application reaches for it."""
    from api import github_routes, source_routes
    from core.sync import sources

    fake = fakes.FakeGitHub()
    for module in (sources, source_routes, github_routes):
        monkeypatch.setattr(module, "GitHubRepository", fake.repository)
    for call in ("verify_token", "exchange_code", "list_repositories"):
        monkeypatch.setattr(github_routes, call, getattr(fake, call))
    return fake


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    # Not used as a context manager: that would run the server's startup, which
    # sweeps jobs and blobs the tests are in the middle of using.
    return TestClient(main.app)


@pytest.fixture
def record(request):
    """Write down what the test actually saw, for the report."""
    request.node.observed = []
    return lambda value: request.node.observed.append(str(value))


# ----- Results: one row per test, for the report -----

results: dict[str, dict] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    row = results.setdefault(item.nodeid, {
        "nodeid": item.nodeid,
        "case": getattr(item.function, "case", None),
        "description": " ".join((item.function.__doc__ or "").split()),
        "outcome": "passed",
        "failure": None,
        "duration": 0.0,
    })
    row["duration"] += report.duration
    row["observed"] = getattr(item, "observed", [])

    if report.failed:
        row["outcome"] = "failed"
        # The assertion and its message, not the whole traceback.
        crash = getattr(report.longrepr, "reprcrash", None)
        row["failure"] = crash.message if crash else str(report.longrepr)
        row["failed_during"] = report.when
    elif report.skipped and report.when == "setup":
        row["outcome"] = "skipped"


def pytest_sessionfinish(session):
    with engine.connect() as connection:
        postgres = connection.execute(text("SHOW server_version")).scalar()

    RESULTS_FILE.parent.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps({
        "environment": {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "os": operating_system(),
            "python": platform.python_version(),
            "pytest": pytest.__version__,
            "database": f"PostgreSQL {postgres}, database '{TEST_DATABASE}'",
            "command": "python -m pytest " + " ".join(sys.argv[1:]),
            "blocked_at_import": blocked_at_import,
        },
        "tests": list(results.values()),
    }, indent=1), encoding="utf-8")
