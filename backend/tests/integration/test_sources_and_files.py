"""Upload refusals, the sides of an analysis, and getting files back out."""

import io
import json
import os
import time
import zipfile

from sqlalchemy import select

from core.db.models import Analysis, Artifact, ArtifactFile
from core.projects import artifact_store, uploads
from tests.helpers import (
    CODE, analyse_and_save, analysis_path, get, new_project, pending_uploads, side_ids, sign_up,
)
from tests.recorder import case

TEXT = ("req.txt", b"A visitor performs a login.", "text/plain")
JAVA = ("A.java", b"class A { void login() {} }", "text/plain")
REQUIREMENT = {"id": "s", "name": "reqs", "kind": "requirements", "file_indexes": [0]}
CODE_FILE = {"id": "t", "name": "code", "kind": "code", "file_indexes": [1]}


def upload(client, artifacts, files, source=("s",), target=("t",), paths=None, raw_artifacts=None):
    """Post an upload exactly as given, however malformed."""
    response = client.post("/analyze/upload", data={
        "artifacts": raw_artifacts if raw_artifacts is not None else json.dumps(artifacts),
        "source_artifact_ids": json.dumps(list(source)),
        "target_artifact_ids": json.dumps(list(target)),
        "file_paths": json.dumps(paths or [file[0] for file in files]),
    }, files=[("files", file) for file in files])
    return response.status_code, response.json().get("detail")


@case(
    id="I-17",
    feature="Upload / refusals",
    level="integration",
    priority="High",
    why="Bad input must be refused with a message the user can act on, before a job is started, and must not leave half-written files on the server.",
    preconditions="Per-file limit lowered to 1,000 bytes for the oversized-file case (30 MB in production)",
    input="11 malformed uploads: no artifacts, invalid JSON, one artifact on both sides, unknown kind, two kinds on one side, wrong extension, a zip for a kind that takes none, a file that is not a zip, a file index out of range, an oversized file, and pasted text for code",
    expected="400 for each, except 413 for the oversized file. No working folder is left behind by any of them",
)
def test_malformed_uploads_are_refused_and_leave_nothing_behind(client, monkeypatch, record):
    """Each kind of bad upload is refused with its own message and cleaned up."""
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)
    before = pending_uploads()
    both = [REQUIREMENT, CODE_FILE]
    md = ("notes.md", b"# notes", "text/plain")
    not_zip = ("code.zip", b"this is not a zip", "application/zip")
    big = ("big.txt", b"x" * 1500, "text/plain")

    answers = {
        "no artifacts": upload(client, [], []),
        "invalid JSON": upload(client, None, [], raw_artifacts="{"),
        "one artifact on both sides": upload(client, [REQUIREMENT], [TEXT], target=("s",)),
        "unknown kind": upload(client, [{**REQUIREMENT, "kind": "nope"}, CODE_FILE], [TEXT, JAVA]),
        "two kinds on one side": upload(client, both + [{**CODE_FILE, "id": "s2"}], [TEXT, JAVA], source=("s", "s2")),
        "wrong extension": upload(client, both, [TEXT, md]),
        "zip not accepted": upload(client, both, [("reqs.zip", b"PK", "application/zip"), JAVA]),
        "not a zip": upload(client, both, [TEXT, not_zip]),
        "index out of range": upload(client, [{**REQUIREMENT, "file_indexes": [7]}, CODE_FILE], [TEXT, JAVA]),
        "oversized file": upload(client, both, [big, JAVA]),
        "pasted text for code": upload(client, [REQUIREMENT, {"id": "t", "name": "c", "kind": "code", "text": "class A"}], [TEXT]),
    }
    for name, (status, detail) in answers.items():
        record(f"{name}: {status} - {detail}")
    record(f"working folders left behind: {sorted(pending_uploads() - before)}")

    expected = {name: 400 for name in answers} | {"oversized file": 413}
    assert {name: status for name, (status, _) in answers.items()} == expected
    assert all(detail for _, detail in answers.values())
    assert pending_uploads() == before


@case(
    id="I-22",
    feature="Deleting an analysis / shared files through the API",
    level="integration",
    priority="High",
    why="Deleting an old run is routine clean-up. It must not make the version's files - which a re-run reads too - impossible to download or run again.",
    preconditions="An analysis with a run and a re-run of it in version 1, whose stored files are older than the collector's grace period",
    input="DELETE the original analysis; then download the re-run's artifact and re-run it again",
    expected="204; the original is gone (404); the re-run's files are still available, its artifact downloads (200), and running it again succeeds (201)",
)
def test_deleting_a_run_leaves_its_reruns_usable(client, db, record):
    """Deleting one analysis does not take away files another analysis still uses."""
    headers = sign_up(client)
    project = new_project(client, headers)
    original = analyse_and_save(client, headers, project)
    rerun = client.post(f"/analyses/{original['analysis_id']}/rerun", headers=headers, json={}).json()

    digests = db.scalars(
        select(ArtifactFile.sha256).join(Artifact)
        .join(Analysis, Analysis.version_id == Artifact.version_id)
        .where(Analysis.analysis_id == rerun["analysis_id"])
    ).all()
    old = time.time() - 2 * 3600
    for digest in digests:
        os.utime(artifact_store.blob_path(digest), (old, old))

    deleted = client.delete(f"/analyses/{original['analysis_id']}", headers=headers)
    statuses = {
        "delete": deleted.status_code,
        "original afterwards": client.get(f"/analyses/{original['analysis_id']}", headers=headers).status_code,
        "download re-run artifact": client.get(
            f"/artifacts/{rerun['artifacts'][0]['artifact_id']}/download", headers=headers).status_code,
        "run it again": client.post(f"/analyses/{rerun['analysis_id']}/rerun", headers=headers, json={}).status_code,
    }
    available = [a["files_available"] for a in get(client, headers, f"/analyses/{rerun['analysis_id']}")["artifacts"]]
    record(statuses)
    record(f"re-run's files still stored: {sum(artifact_store.blob_exists(d) for d in digests)} of {len(digests)}; "
           f"files_available: {available}")

    assert statuses == {"delete": 204, "original afterwards": 404, "download re-run artifact": 200, "run it again": 201}
    assert all(available) and all(artifact_store.blob_exists(d) for d in digests)


@case(
    id="I-19",
    feature="Sides / listing and refusals",
    level="integration",
    priority="Medium",
    why="Requests about sides that do not exist, or that cannot work, must fail cleanly before anything is stored.",
    preconditions="An analysis with a requirements side and a code side",
    input="List the sides. Then stage files for a side id that does not exist, stage with no files, take the requirements side from GitHub, and list the sides of an analysis id that does not exist",
    expected="2 sides listed, source first. The four bad requests give 404, 400, 400 and 404",
)
def test_sides_are_listed_and_bad_requests_refused(client, record):
    """An analysis lists its two sides, and requests that make no sense are refused."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    base = f"{analysis_path(project, config)}/sources"
    sides = get(client, headers, base)
    requirements = side_ids(client, headers, project, config)["source"]
    record(f"sides: {[(s['role'], s['kind'], s['name']) for s in sides]}")

    file = [("files", ("UC1.txt", b"text", "text/plain"))]
    bad = {
        "stage for unknown": client.post(f"{base}/999999/files", headers=headers, files=file).status_code,
        "stage no files": client.post(f"{base}/{requirements}/files", headers=headers).status_code,
        "GitHub for requirements": client.post(f"{base}/{requirements}/github", headers=headers,
                                               json={"repository": "owner/repo"}).status_code,
        "unknown analysis": client.get(f"{analysis_path(project, 999999)}/sources", headers=headers).status_code,
    }
    record(f"bad requests: {bad}")

    assert [(s["role"], s["kind"]) for s in sides] == [("source", "requirements"), ("target", "code")]
    assert list(bad.values()) == [404, 400, 400, 404]


@case(
    id="I-25",
    feature="Artifacts / download",
    level="integration",
    priority="Medium",
    why="Stored artifacts are the only copy the tool keeps of what a run analysed. What comes back must be exactly what went in.",
    preconditions="A saved analysis whose code artifact holds Auth.java and Loans.java",
    input="GET /artifacts/{id}/download for the code artifact",
    expected="200, a zip named for the artifact, containing exactly Auth.java and Loans.java with the uploaded contents",
)
def test_downloaded_artifact_matches_what_was_uploaded(client, record):
    """An artifact downloads as a zip holding the same files, byte for byte."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    artifact = next(a for a in get(client, headers, f"/analyses/{saved['analysis_id']}")["artifacts"]
                    if a["artifact_type"] == "code")

    response = client.get(f"/artifacts/{artifact['artifact_id']}/download", headers=headers)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        contents = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
    record(f"status {response.status_code}; {response.headers['content-disposition']}")
    record(f"files in the zip: {sorted(contents)}; identical to the upload: {contents == CODE[2]}")

    assert response.status_code == 200
    assert contents == CODE[2]
