"""A TraceRAG server for the Playwright UI tests, with fake models and a fake GitHub.

    python tests/e2e_server.py PORT ORIGIN   serve on PORT, for a frontend at ORIGIN
    python tests/e2e_server.py --drop        drop the database and the folders again

Like tests/conftest.py, everything that decides which database and which
folders the application uses is set before the application is imported. The
database is a separate one, made empty on every start; the stored files and
vector indexes go to a folder under the system temp directory. Nothing in the
application is changed: the fakes are patched in from here.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, make_url, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATABASE = "tracerag_e2e"
WORK_DIR = Path(tempfile.gettempdir()) / "tracerag-e2e"

# A requirement holding this makes the classifier fail, so a test can play
# out a model that stops answering in the middle of an update.
OUTAGE = "CLASSIFIER-OUTAGE"


def server_url():
    """The development database's address, with the e2e database's name."""
    for line in (BACKEND_DIR / ".env").read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip().upper() == "DATABASE_URL":
            development = make_url(value.strip().strip("\"'"))
            if development.database == DATABASE:
                raise RuntimeError(f"backend/.env points at '{DATABASE}', which this launcher drops.")
            return development
    raise RuntimeError("backend/.env has no DATABASE_URL to take the server from.")


def drop() -> None:
    server = create_engine(server_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
    server.dispose()
    shutil.rmtree(WORK_DIR, ignore_errors=True)


def create() -> str:
    drop()
    server = create_engine(server_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{DATABASE}"'))
    server.dispose()
    (WORK_DIR / "storage").mkdir(parents=True)
    (WORK_DIR / "cwd").mkdir(parents=True)
    return server_url().set(database=DATABASE).render_as_string(hide_password=False)


def serve(port: int, origin: str) -> None:
    os.environ.update({
        "DATABASE_URL": create(),
        "STORAGE_PATH": str(WORK_DIR / "storage"),
        "SECRET_KEY": "tracerag-e2e-secret-key-0123456789abcdef",
        "GITHUB_CLIENT_ID": "e2e-client-id",
        "GITHUB_CLIENT_SECRET": "e2e-client-secret",
        "GITHUB_TOKEN": "",
        "GROQ_API_KEYS": "not-a-real-key",
        "FRONTEND_URL": origin,
        "ANONYMIZED_TELEMETRY": "False",
        "HF_HUB_OFFLINE": "1",
    })
    # The vector indexes are written to ./chroma_data.
    os.chdir(WORK_DIR / "cwd")
    sys.path.insert(0, str(BACKEND_DIR))

    import uvicorn
    from fastapi.middleware.cors import CORSMiddleware

    import main
    from api import github_routes, pipeline_factory, source_routes
    from core.sync import sources
    from tests import fakes

    class Classifier(fakes.FakeClassifier):
        def _ask(self, source, target):
            if OUTAGE in source.content:
                raise RuntimeError("The classifier stopped answering.")
            return super()._ask(source, target)

    pipeline_factory.OllamaEmbeddingCreator = lambda **_: fakes.FakeEmbedder()
    pipeline_factory.get_classifier = lambda classifier_type, use_cache: Classifier(
        "e2e-classifier" if use_cache else None
    )
    pipeline_factory.get_chat_provider = fakes.FakeChatProvider
    github = fakes.FakeGitHub()
    for module in (sources, source_routes, github_routes):
        module.GitHubRepository = github.repository
    for call in ("verify_token", "exchange_code", "list_repositories"):
        setattr(github_routes, call, getattr(github, call))

    # The app allows the usual Vite port; the tests' frontend runs on another.
    for middleware in main.app.user_middleware:
        if middleware.cls is CORSMiddleware:
            middleware.kwargs["allow_origins"] = [*middleware.kwargs["allow_origins"], origin]

    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    if sys.argv[1:] == ["--drop"]:
        drop()
    else:
        serve(int(sys.argv[1]), sys.argv[2])
