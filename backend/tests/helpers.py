"""Small builders the tests share, so each test reads as what it checks."""

import json
import shutil
import uuid
from pathlib import Path

from api.schemas import AnalysisConfig, AnalyzeResponse, ElementResponse, TraceLinkResponse
from core.auth import create_user
from core.ingestion import CodeProvider, DocumentProvider
from core.pipeline import TracePipeline
from core.preprocessing import ArtifactPreprocessor, CodeMethodPreprocessor
from core.projects import artifact_store, service
from tests.fakes import FakeClassifier, FakeEmbedder

DATA = Path(__file__).resolve().parent / "data"

# What the fake classifier links in the corpus under tests/data: each use case
# names one method, and UC4 names nothing the code does.
EXPECTED_LINKS = {
    ("UC1.txt", "Auth.java::Auth::login"),
    ("UC2.txt", "Auth.java::Auth::logout"),
    ("UC3.txt", "Loans.java::Loans::borrow"),
}


def unique(prefix: str) -> str:
    """A name no other test has used, so tests cannot see each other's rows."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def short(identifier: str) -> str:
    """An identifier without its folder or its parameter list, for comparing links.

    "…/target/Auth.java::Auth::login(String name)" -> "Auth.java::Auth::login"
    """
    path, separator, member = identifier.partition("::")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return f"{name}{separator}{member.split('(')[0]}"


def link_pairs(links) -> set[tuple[str, str]]:
    """Links as (source, target) names, from pipeline objects or from API responses."""
    def ends(link):
        if isinstance(link, dict):
            return link["source_id"], link["target_id"]
        return link.source_id, link.target_id

    return {(short(source), short(target)) for source, target in map(ends, links)}


# ----- The pipeline on its own, for the unit tests -----

def copy_corpus(destination: Path) -> tuple[Path, Path]:
    """Lay the corpus out as a run's workspace: requirements one side, code the other."""
    source, target = destination / "source", destination / "target"
    shutil.copytree(DATA / "req", source)
    shutil.copytree(DATA / "code", target)
    return source, target


def build_pipeline(source: Path, target: Path, chroma: Path, **settings) -> TracePipeline:
    """A pipeline over the corpus with the fake models: whole documents against methods."""
    settings.setdefault("classifier", FakeClassifier())
    return TracePipeline(
        source_provider=DocumentProvider(str(source)),
        target_provider=CodeProvider(str(target)),
        source_preprocessor=ArtifactPreprocessor(),
        target_preprocessor=CodeMethodPreprocessor(),
        embedder=FakeEmbedder(),
        source_kind="requirements",
        target_kind="code",
        config_key="unit-test",
        chroma_path=str(chroma),
        workspace_roots=[source, target],
        **settings,
    )


# ----- The service layer, for the graph tests -----

# Whole documents against methods: the configuration most tests run under.
CONFIG = AnalysisConfig(
    source_preprocessor="single",
    target_preprocessor="method",
    source_output_level="artifact",
    target_output_level="function",
    classifier="reasoning",
    n_results=10,
    dependency_expansion_depth=1,
    summarize_elements=False,
)


def make_user(db):
    return create_user(db, "Test User", f"{unique('user')}@example.com", "a-test-password")


def make_project(db, user=None, name: str = "project"):
    user = user or make_user(db)
    return service.create_project(db, user.user_id, unique(name), None)


def result_of(sources, targets, links, unimplemented=()) -> AnalyzeResponse:
    """What a run found, built by hand: element names, and (source, target, score) links."""
    def elements(names, level):
        return [
            ElementResponse(identifier=name, content=f"content of {name}", level=level, type="t")
            for name in names
        ]

    return AnalyzeResponse(
        trace_links=[
            TraceLinkResponse(
                source_id=source, target_id=target, confidence=score,
                confidence_level="high" if score >= 0.85 else "medium" if score >= 0.7 else "low",
            )
            for source, target, score in links
        ],
        source_elements=elements(sources, "artifact"),
        target_elements=elements(targets, "function"),
        unimplemented=[{"identifier": name} for name in unimplemented],
        summary={},
        element_links=[(source, target) for source, target, _ in links],
    )


def save_run(db, project, result, version=None, config=CONFIG, kinds=("requirements", "code"),
             renames=None, upload_id=None, keep_files=False):
    """Save a run as the application does: its analysis, its files, then its graph."""
    version = version or service.next_version(db, project)
    analysis = service.save_analysis(
        db, project, note=None, config=config, result=result, execution_duration=1.0,
        version_id=version.version_id, source_kind=kinds[0], target_kind=kinds[1],
    )
    if upload_id:
        service.claim_artifacts(db, analysis, upload_id, keep_files=keep_files)
    service.update_graph(db, analysis, result, source_kind=kinds[0], target_kind=kinds[1], renames=renames)
    db.refresh(analysis)
    return analysis


def graph_edges(db, config_id: int) -> dict[tuple[str, str], object]:
    """A configuration's links, keyed by the two identifiers each one joins."""
    rows, _ = service.list_graph_edges(db, config_id, limit=1000)
    return {(source.identifier, target.identifier): edge for edge, source, target in rows}


def make_upload(sides: dict) -> str:
    """A pending upload, as the analysis endpoint leaves one behind.

    `sides` maps a role to (kind, name, {relative path: text}).
    """
    upload_id, folder = artifact_store.create_upload_dir()
    manifest = []
    for role, (kind, name, files) in sides.items():
        for path, text in files.items():
            target = folder / role / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        manifest.append({"role": role, "artifact_type": kind, "name": name, "directory": role})
    artifact_store.write_manifest(folder, manifest)
    return upload_id


# ----- The HTTP API, for the integration tests -----

def files_in(folder: str) -> dict[str, str]:
    """The corpus files under tests/data/<folder>, as {name: text}."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((DATA / folder).iterdir()) if path.is_file()
    }


# One side of a run, as (kind, name, {path: text}).
REQUIREMENTS = ("requirements", "reqs", files_in("req"))
CODE = ("code", "code", files_in("code"))
ARCHITECTURE_DOCUMENT = ("architecture_document", "architecture notes", {"arch.txt": files_in(".")["arch.txt"]})

# The same run the graph tests use, as the form fields the upload endpoint takes.
SETTINGS = {
    "source_preprocessor": "single",
    "target_preprocessor": "method",
    "source_output_level": "artifact",
    "target_output_level": "function",
    "classifier": "reasoning",
    "n_results": 10,
    "dependency_expansion_depth": 1,
    "summarize_elements": False,
}


def sign_up(client) -> dict:
    """Register a new user and return the headers that sign their requests."""
    response = client.post("/auth/register", json={
        "full_name": "Test User", "email": f"{unique('user')}@example.com", "password": "a-test-password",
    })
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def new_project(client, headers, name: str = "project") -> int:
    response = client.post("/projects", headers=headers, json={"project_name": unique(name)})
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def upload_form(source, target, settings: dict) -> tuple[dict, list]:
    """The form fields and file parts of one upload: each side is (kind, name, {path: text})."""
    artifacts, parts, paths = [], [], []
    for role, (kind, name, files) in (("s", source), ("t", target)):
        indexes = []
        for path, text in files.items():
            indexes.append(len(parts))
            parts.append(("files", (Path(path).name, text.encode("utf-8"), "text/plain")))
            paths.append(path)
        artifacts.append({"id": role, "name": name, "kind": kind, "file_indexes": indexes})

    form = {
        "artifacts": json.dumps(artifacts),
        "source_artifact_ids": '["s"]',
        "target_artifact_ids": '["t"]',
        "file_paths": json.dumps(paths),
        **{name: str(value).lower() if isinstance(value, bool) else str(value)
           for name, value in settings.items()},
    }
    return form, parts


def start_analysis(client, headers=None, project_id=None, source=REQUIREMENTS, target=CODE, **settings):
    """Post an upload and return the raw response, for tests about refusals."""
    form, parts = upload_form(source, target, {**SETTINGS, **settings})
    form["analysis_mode"] = "project" if project_id else "session"
    if project_id:
        form["project_id"] = str(project_id)
    return client.post("/analyze/upload", headers=headers or {}, data=form, files=parts)


def run_analysis(client, headers=None, project_id=None, source=REQUIREMENTS, target=CODE, **settings) -> dict:
    """Run an analysis to the end and return its result. The job finishes inside the request."""
    started = start_analysis(client, headers, project_id, source, target, **settings)
    assert started.status_code == 200, started.text
    job = client.get(f"/jobs/{started.json()['job_id']}", params={"token": started.json()["token"]}).json()
    assert job["state"] == "succeeded", job["error"]
    return job["result"]


def save_analysis(client, headers, project_id, result, **settings):
    """Save a finished run into a project, claiming its uploaded files."""
    return client.post(f"/projects/{project_id}/analyses", headers=headers, json={
        "config": {**SETTINGS, **settings},
        "result": result,
        "upload_id": result.get("upload_id"),
        "execution_duration": 1.0,
    })


def analyse_and_save(client, headers, project_id, source=REQUIREMENTS, target=CODE, **settings) -> dict:
    """A New Analysis from start to finish: upload, run, save. Returns the saved summary and the result."""
    result = run_analysis(client, headers, project_id, source, target, **settings)
    saved = save_analysis(client, headers, project_id, result, **settings)
    assert saved.status_code == 201, saved.text
    return {**saved.json(), "result": result}


def get(client, headers, path: str, **params):
    response = client.get(path, headers=headers, params=params)
    assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"
    return response.json()


def source_ids(client, headers, project_id) -> dict[str, int]:
    """The project's connected sources, as {kind: source id}."""
    return {s["kind"]: s["source_id"] for s in get(client, headers, f"/projects/{project_id}/sources")}


def stage(client, headers, project_id, source_id, files: dict[str, str]):
    """Hand over new files for an uploaded source, ready for the next sync."""
    return client.post(
        f"/projects/{project_id}/sources/{source_id}/files", headers=headers,
        data={"file_paths": json.dumps(list(files))},
        files=[("files", (Path(path).name, text.encode("utf-8"), "text/plain")) for path, text in files.items()],
    )


def sync(client, headers, project_id, source_kind="requirements", target_kind="code", **body) -> dict:
    """Ask for a sync. Returns the answer, with the finished job under 'job' if one was started."""
    response = client.post(f"/projects/{project_id}/sync", headers=headers, json={
        "source_kind": source_kind, "target_kind": target_kind, **body,
    })
    answer = {"status": response.status_code, **response.json()}
    if answer.get("job_id"):
        answer["job"] = get(client, headers, f"/jobs/{answer['job_id']}")
    return answer


def pending_uploads() -> set[str]:
    """The working folders on disk right now. A finished run or sync should leave none behind."""
    root = artifact_store.UPLOAD_ROOT
    return {path.name for path in root.iterdir()} if root.exists() else set()
