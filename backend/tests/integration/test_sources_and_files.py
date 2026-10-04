"""Upload refusals, the sources a project holds, and getting files back out."""

import io
import json
import os
import time
import zipfile

from sqlalchemy import select

from core.db.models import Artifact, ArtifactFile
from core.projects import artifact_store, uploads
from tests.helpers import (
    CODE, REQUIREMENTS, analyse_and_save, get, new_project, pending_uploads, sign_up, source_ids,
    stage, sync,
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
    input="12 malformed uploads: no artifacts, invalid JSON, one artifact on both sides, unknown kind, two kinds on one side, wrong extension, a zip for a kind that takes none, a file that is not a zip, a file index out of range, an oversized file, pasted text for code, and a connected source without a login",
    expected="400 for each, except 413 for the oversized file and 401 for the connected source without a login. No working folder is left behind by any of them",
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
        "connected source, no login": upload(client, [{"id": "s", "name": "r", "kind": "requirements", "source_id": 1}, CODE_FILE], [TEXT, JAVA]),
    }
    for name, (status, detail) in answers.items():
        record(f"{name}: {status} - {detail}")
    record(f"working folders left behind: {sorted(pending_uploads() - before)}")

    expected = {name: 400 for name in answers} | {"oversized file": 413, "connected source, no login": 401}
    assert {name: status for name, (status, _) in answers.items()} == expected
    assert all(detail for _, detail in answers.values())
    assert pending_uploads() == before


@case(
    id="I-18",
    feature="Sources / uploading a kind the project already has",
    level="integration",
    priority="High",
    why="This is what the re-upload confirmation in the UI warns about: saving puts the new files in place of the project's source for that kind.",
    preconditions="A project whose requirements source is 'reqs' and code source is 'code'",
    input="A second New Analysis whose requirements are uploaded under the name 'second draft', with the same code",
    expected="The project lists 2 active sources: requirements 'second draft' and code 'code'. 'reqs' is still listed when disconnected ones are included, as inactive",
)
def test_second_upload_of_a_kind_replaces_the_source(client, record):
    """After a second upload of requirements under a new name, that upload is the project's requirements source."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    analyse_and_save(client, headers, project, source=("requirements", "second draft", REQUIREMENTS[2]))

    active = [(s["kind"], s["name"]) for s in get(client, headers, f"/projects/{project}/sources")]
    everything = {(s["kind"], s["name"]): s["is_active"]
                  for s in get(client, headers, f"/projects/{project}/sources", include_disconnected="true")}
    record(f"active sources: {active}")
    record(f"all sources: {everything}")

    assert sorted(active) == [("code", "code"), ("requirements", "second draft")]
    assert everything == {
        ("requirements", "reqs"): False, ("code", "code"): True, ("requirements", "second draft"): True,
    }


@case(
    id="I-22",
    feature="Deleting an analysis / shared files through the API",
    level="integration",
    priority="High",
    why="Deleting an old run is routine clean-up. It must not make a re-run of it impossible to download or run again.",
    preconditions="An analysis and a re-run of it that share stored files older than the collector's grace period",
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
        select(ArtifactFile.sha256).join(Artifact).where(Artifact.analysis_id == rerun["analysis_id"])
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
    feature="Sources / listing, disconnecting and reconnecting",
    level="integration",
    priority="Medium",
    why="A disconnected source must stay visible so it can be brought back, and requests about sources that do not exist must fail cleanly.",
    preconditions="A project with a requirements source and a code source, both recorded by version 1",
    input="Disconnect requirements; list sources with and without disconnected ones; read the pair; reconnect. Then disconnect, reconnect and stage files for a source id that does not exist, stage with no files, and connect a repository for an unknown kind",
    expected="Disconnect is kept (removed=false) and explained. The default list hides it, the full list shows it inactive, and the pair has no source side. Reconnect makes it active again. The five bad requests give 404, 404, 404, 400, 400",
)
def test_sources_can_be_disconnected_seen_and_reconnected(client, record):
    """A disconnected source is hidden from syncing but still listed, and can be reconnected."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]
    base = f"/projects/{project}/sources"

    removal = client.delete(f"{base}/{requirements}", headers=headers).json()
    listed = len(get(client, headers, base))
    full = {s["kind"]: s["is_active"] for s in get(client, headers, base, include_disconnected="true")}
    pair = get(client, headers, f"/projects/{project}/pairs")[0]
    reconnected = client.post(f"{base}/{requirements}/reconnect", headers=headers).json()
    record(f"disconnect: removed={removal['removed']} - {removal['detail']}")
    record(f"default list: {listed} source; full list: {full}; pair's source side: {pair['source']}")
    record(f"reconnect: is_active={reconnected['is_active']}")

    file = [("files", ("UC1.txt", b"text", "text/plain"))]
    bad = {
        "disconnect unknown": client.delete(f"{base}/999999", headers=headers).status_code,
        "reconnect unknown": client.post(f"{base}/999999/reconnect", headers=headers).status_code,
        "stage for unknown": client.post(f"{base}/999999/files", headers=headers, files=file).status_code,
        "stage no files": client.post(f"{base}/{requirements}/files", headers=headers).status_code,
        "connect unknown kind": client.post(f"{base}/github", headers=headers,
                                            json={"kind": "nope", "repository": "owner/repo"}).status_code,
    }
    record(f"bad requests: {bad}")

    assert removal["removed"] is False and "1 saved version" in removal["detail"]
    assert listed == 1 and full == {"requirements": False, "code": True}
    assert pair["source"] is None and pair["target"]["kind"] == "code"
    assert reconnected["is_active"] is True
    assert list(bad.values()) == [404, 404, 404, 400, 400]


@case(
    id="I-23",
    feature="Comparison / compare endpoint",
    level="integration",
    priority="Medium",
    why="The Compare page shows what a change did to the links. It must diff the right two runs and refuse comparisons that mean nothing.",
    preconditions="Run 1 over UC1-UC4. Run 2 is a sync after UC5 (about the passphrase) was added",
    input="GET /analyses/{run1}/compare/{run2}; then compare a run with itself, and with a run from another project",
    expected="added 1 (UC5->login), removed 0, unchanged 3, comparable. Same run: 400. Other project: 409",
)
def test_compare_reports_what_changed_between_two_runs(client, record):
    """Two runs of one project are diffed link by link; anything else is refused."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]
    staged = stage(client, headers, project, requirements,
                   {**REQUIREMENTS[2], "UC5.txt": "A passphrase is required.\n"}).json()
    second = sync(client, headers, project, replacements={requirements: staged["upload_id"]})
    head = second["job"]["result"]["configs"][0]["analysis_id"]
    elsewhere = analyse_and_save(client, headers, new_project(client, headers))

    diff = get(client, headers, f"/analyses/{first['analysis_id']}/compare/{head}")
    same = client.get(f"/analyses/{head}/compare/{head}", headers=headers).status_code
    other = client.get(f"/analyses/{head}/compare/{elsewhere['analysis_id']}", headers=headers).status_code
    record(f"summary: {diff['summary']}; comparable: {diff['comparable']}")
    record(f"added: {[(l['source_id'], l['target_id'].split('::')[-1]) for l in diff['added']]}")
    record(f"same run: {same}; run from another project: {other}")

    assert diff["summary"]["added"] == 1 and diff["summary"]["removed"] == 0
    assert diff["summary"]["unchanged"] == 3 and diff["comparable"]
    assert diff["added"][0]["source_id"] == "UC5.txt" and "login" in diff["added"][0]["target_id"]
    assert (same, other) == (400, 409)


@case(
    id="I-32",
    feature="Comparison / removed requirements",
    level="integration",
    priority="Medium",
    why="'Newly implemented' is read as progress. A requirement that was deleted has not been implemented, and listing it there misreports coverage.",
    preconditions="Run 1 has UC4 with no link (unimplemented)",
    input="Sync with requirements that no longer include UC4, then compare run 1 with the new run",
    expected="UC4.txt is not listed under newly_implemented",
)
def test_a_deleted_requirement_is_not_reported_as_newly_implemented(client, record):
    """A requirement that disappeared between two runs did not become implemented."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]
    files = {name: text for name, text in REQUIREMENTS[2].items() if name != "UC4.txt"}
    staged = stage(client, headers, project, requirements, files).json()
    second = sync(client, headers, project, replacements={requirements: staged["upload_id"]})
    head = second["job"]["result"]["configs"][0]["analysis_id"]

    diff = get(client, headers, f"/analyses/{first['analysis_id']}/compare/{head}")
    record(f"newly_implemented: {diff['newly_implemented']}; newly_unimplemented: {diff['newly_unimplemented']}")

    assert "UC4.txt" not in diff["newly_implemented"]


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
