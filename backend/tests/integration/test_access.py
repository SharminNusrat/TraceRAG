"""Who may see and change what: accounts, other users' data, and anonymous callers."""

import json

from tests.helpers import (
    SETTINGS, analyse_and_save, get, new_project, sign_up, source_ids, start_analysis, unique,
)
from tests.recorder import case

PAIR = {"source_kind": "requirements", "target_kind": "code"}


@case(
    id="I-20",
    feature="Access control / a second user",
    level="integration",
    priority="Critical",
    why="Ids count up from 1, so they are guessable. One route that forgets to check the owner lets any user read, sync or delete another user's project.",
    preconditions="User A owns a project with two saved runs, sources, a job and stored artifacts. User B is a different signed-in user",
    input="As B, call every project, analysis, source, sync, artifact and job endpoint with A's ids (21 requests). Then repeat 5 of them with no login",
    expected="Every request by B is refused (404, or 401/403) and none returns A's data. Anonymous requests get 401. B's own lists are empty. A's project, runs and sources are unchanged afterwards",
)
def test_a_second_user_cannot_reach_the_first_users_data(client, github, record):
    """Another signed-in user is refused on every endpoint that takes an id belonging to someone else."""
    owner, intruder = sign_up(client), sign_up(client)
    project = new_project(client, owner)
    first = analyse_and_save(client, owner, project)
    second = client.post(f"/analyses/{first['analysis_id']}/rerun", headers=owner, json={}).json()
    source = source_ids(client, owner, project)["requirements"]
    artifact = second["artifacts"][0]["artifact_id"]
    job = start_analysis(client, owner, project).json()["job_id"]
    a, b = first["analysis_id"], second["analysis_id"]
    file = [("files", ("UC1.txt", b"changed", "text/plain"))]
    save_body = {"config": SETTINGS, "result": first["result"]}

    attempts = [
        ("GET", f"/projects/{project}", {}),
        ("GET", f"/projects/{project}/configs", {}),
        ("GET", f"/projects/{project}/versions", {}),
        ("GET", f"/projects/{project}/graph", {}),
        ("GET", f"/projects/{project}/sources", {}),
        ("GET", f"/projects/{project}/pairs", {}),
        ("GET", f"/projects/{project}/sync/status", {"params": PAIR}),
        ("POST", f"/projects/{project}/sync", {"json": {**PAIR, "force": True}}),
        ("POST", f"/projects/{project}/analyses", {"json": save_body}),
        ("POST", f"/projects/{project}/sources/github", {"json": {"kind": "code", "repository": "owner/library"}}),
        ("POST", f"/projects/{project}/sources/{source}/files", {"files": file}),
        ("POST", f"/projects/{project}/sources/{source}/reconnect", {}),
        ("DELETE", f"/projects/{project}/sources/{source}", {}),
        ("GET", f"/analyses/{a}", {}),
        ("GET", f"/analyses/{a}/compare/{b}", {}),
        ("POST", f"/analyses/{a}/rerun", {"json": {}}),
        ("DELETE", f"/analyses/{a}", {}),
        ("GET", f"/artifacts/{artifact}/download", {}),
        ("GET", f"/jobs/{job}", {}),
        ("DELETE", f"/projects/{project}", {}),
    ]
    statuses = {
        f"{method} {path}": client.request(method, path, headers=intruder, **options).status_code
        for method, path, options in attempts
    }
    # Running an analysis that reads A's stored requirements, from B's account.
    statuses["POST /analyze/upload reading A's source"] = client.post("/analyze/upload", headers=intruder, data={
        "artifacts": json.dumps([
            {"id": "s", "name": "reqs", "kind": "requirements", "source_id": source},
            {"id": "t", "name": "code", "kind": "code", "file_indexes": [0]},
        ]),
        "source_artifact_ids": '["s"]', "target_artifact_ids": '["t"]',
        "file_paths": '["A.java"]', "analysis_mode": "project", "project_id": str(project),
    }, files=[("files", ("A.java", b"class A {}", "text/plain"))]).status_code

    allowed = {name: status for name, status in statuses.items() if status not in (401, 403, 404)}
    record(f"{len(statuses)} requests as another user; status codes seen: {sorted(set(statuses.values()))}")
    record(f"requests that were NOT refused: {allowed or 'none'}")

    anonymous = {
        path: client.get(path).status_code
        for path in (f"/projects/{project}", f"/analyses/{a}", f"/projects/{project}/sources",
                     f"/artifacts/{artifact}/download", "/projects")
    }
    own_lists = (len(get(client, intruder, "/projects")), len(get(client, intruder, "/analyses")))
    record(f"anonymous: {sorted(set(anonymous.values()))}; B's own (projects, analyses): {own_lists}")

    intact = get(client, owner, f"/projects/{project}")
    sources = get(client, owner, f"/projects/{project}/sources")
    record(f"A's project afterwards: {len(intact['analyses'])} runs, {len(sources)} active sources")

    assert allowed == {}
    assert set(statuses.values()) <= {404}, statuses
    assert set(anonymous.values()) == {401}
    assert own_lists == (0, 0)
    assert len(intact["analyses"]) == 2 and len(sources) == 2


@case(
    id="I-21",
    feature="Access control / anonymous jobs",
    level="integration",
    priority="High",
    why="A run made without an account is followed by its token. Job ids are sequential, so without the token anyone could read anyone's results.",
    input="Start a run with no login. Read its job with the right token, a wrong token, no token, and as a different signed-in user",
    expected="Only the right token gets 200 with the result; the other three get 404",
)
def test_anonymous_job_is_readable_only_with_its_token(client, record):
    """An anonymous run's job and result can be read by the token holder and nobody else."""
    started = start_analysis(client).json()
    job, token = started["job_id"], started["token"]

    statuses = {
        "right token": client.get(f"/jobs/{job}", params={"token": token}).status_code,
        "wrong token": client.get(f"/jobs/{job}", params={"token": "x" * 32}).status_code,
        "no token": client.get(f"/jobs/{job}").status_code,
        "another signed-in user": client.get(f"/jobs/{job}", headers=sign_up(client)).status_code,
    }
    record(statuses)

    assert statuses == {"right token": 200, "wrong token": 404, "no token": 404, "another signed-in user": 404}


@case(
    id="I-29",
    feature="Access control / path-based analysis",
    level="integration",
    priority="High",
    why="POST /analyze reads files from a folder path on the server. Open to anyone, it returns the contents of any folder the server can read.",
    preconditions="A folder on the server, outside any project, holding a file with the text 'TOP-SECRET-VALUE'",
    input="POST /analyze with no login, naming that folder as the source path",
    expected="The request is refused (401 or 403) and the file's contents are not returned",
)
def test_path_based_analysis_requires_a_login(client, tmp_path, record):
    """An anonymous caller must not be able to make the server read a folder of its choosing."""
    secret = tmp_path / "private"
    secret.mkdir()
    (secret / "notes.txt").write_text("TOP-SECRET-VALUE login passphrase", encoding="utf-8")
    code = tmp_path / "code"
    code.mkdir()
    (code / "A.java").write_text("class A { void login(String passphrase) {} }", encoding="utf-8")

    response = client.post("/analyze", json={
        "source": {"kind": "requirements", "path": str(secret)},
        "target": {"kind": "code", "path": str(code)},
        "source_preprocessor": "single", "analysis_mode": "session", "summarize_elements": False,
    })
    leaked = "TOP-SECRET-VALUE" in response.text
    record(f"status without login: {response.status_code}")
    record(f"file contents returned in the response: {leaked}")

    assert response.status_code in (401, 403), f"anonymous request was answered with {response.status_code}"
    assert not leaked


@case(
    id="I-01",
    feature="Authentication / register, login, identity",
    level="integration",
    priority="High",
    why="Everything else is scoped to the signed-in user. A duplicate account, an accepted wrong password or a leaked hash undermines all of it.",
    input="Register; register the same email again in capitals; log in with the right and the wrong password; a 7-character password; /auth/me with a valid token, no token and a garbage token",
    expected="201; 409; 200; 401; 422; then 200, 401, 401. No response contains a password hash",
)
def test_accounts_register_login_and_identify(client, record):
    """An account is created once, signs in only with its password, and is identified by its token."""
    email = f"{unique('person')}@example.com"
    account = {"full_name": "A Person", "email": email, "password": "correct-horse"}

    registered = client.post("/auth/register", json=account)
    token = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    responses = {
        "register": registered,
        "same email in capitals": client.post("/auth/register", json={**account, "email": email.upper()}),
        "login": client.post("/auth/login", json={"email": email, "password": "correct-horse"}),
        "wrong password": client.post("/auth/login", json={"email": email, "password": "wrong-horse"}),
        "7-character password": client.post("/auth/register", json={**account, "email": f"x{email}", "password": "short12"}),
        "me, valid token": client.get("/auth/me", headers=token),
        "me, no token": client.get("/auth/me"),
        "me, garbage token": client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"}),
    }
    statuses = {name: response.status_code for name, response in responses.items()}
    record(statuses)
    record(f"/auth/me returns: {sorted(responses['me, valid token'].json())}")

    assert statuses == {
        "register": 201, "same email in capitals": 409, "login": 200, "wrong password": 401,
        "7-character password": 422, "me, valid token": 200, "me, no token": 401, "me, garbage token": 401,
    }
    assert responses["me, valid token"].json()["email"] == email
    assert not any("password" in response.text.lower() and "hash" in response.text.lower()
                   for response in responses.values())
