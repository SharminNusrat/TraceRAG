"""GitHub as a source, and the account connection - against a pretend GitHub, never the real one."""

from urllib.parse import parse_qs, urlparse

from config import settings
from core import secrets
from core.db.models import User
from tests.helpers import CODE, analyse_and_save, get, new_project, sign_up, sync
from tests.recorder import case

PAIR = {"source_kind": "requirements", "target_kind": "code"}


def code_status(client, headers, project) -> dict:
    """Where the code source stands, as the sync dialog is told."""
    rows = get(client, headers, f"/projects/{project}/sync/status", **PAIR)
    return next(row for row in rows if row["kind"] == "code")


def connect_account(client, headers, return_to="/app/history") -> str:
    """Go through the OAuth handshake against the pretend GitHub. Returns where the browser is sent."""
    start = get(client, headers, "/github/oauth/start", return_to=return_to)
    state = parse_qs(urlparse(start["authorize_url"]).query)["state"][0]
    callback = client.get("/github/oauth/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    return callback.headers["location"]


def user_of(db, client, headers) -> User:
    db.expire_all()
    return db.get(User, get(client, headers, "/auth/me")["user_id"])


@case(
    id="I-14",
    feature="GitHub sync / fetch, version and follow renames",
    level="integration",
    priority="Critical",
    why="The demo syncs code from GitHub. A new commit must be fetched and versioned, and a moved file must keep its links instead of breaking them.",
    preconditions="A saved requirements->code run from uploaded files. GitHub is replaced by a fake repository holding the same code at commit-1",
    input="Connect the repository as the code source and sync. Then push commit-2, which moves Auth.java to security/Auth.java, and sync again",
    expected="Connecting replaces the uploaded code source. First sync: code fetched at commit-1, version 2, 3 links. Second sync: commit-2 fetched, version 3, still 3 active links now on security/Auth.java, 0 broken, no node gone. Afterwards the source reports no change",
)
def test_github_source_is_fetched_versioned_and_renames_followed(client, github, record):
    """Code connected from GitHub is fetched when it moves, and a renamed file keeps its links."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    github.push("commit-1", CODE[2])

    connected = client.post(f"/projects/{project}/sources/github", headers=headers,
                            json={"kind": "code", "repository": "https://github.com/owner/library"})
    sources = {(s["kind"], s["origin"]): s["is_active"]
               for s in get(client, headers, f"/projects/{project}/sources", include_disconnected="true")}
    before = code_status(client, headers, project)
    record(f"connect: {connected.status_code}, location {connected.json()['location']}, branch {connected.json()['branch']}")
    record(f"sources (kind, origin): active -> {sources}")
    record(f"status before the first sync: changed={before['changed']}, {before['last_sync_ref']} -> {before['latest_ref']}")

    first = sync(client, headers, project)["job"]["result"]
    record(f"first sync: {first['detail']}; code ref {[s['ref'] for s in first['sources'] if s['kind'] == 'code']}")

    moved = {"security/Auth.java": CODE[2]["Auth.java"], "Loans.java": CODE[2]["Loans.java"]}
    github.push("commit-2", moved, renames={"Auth.java": "security/Auth.java"})
    between = code_status(client, headers, project)
    second = sync(client, headers, project)["job"]["result"]
    graph = get(client, headers, f"/projects/{project}/graph")
    after = code_status(client, headers, project)
    targets = sorted(link["to_identifier"].split("::")[0] for link in graph["links"])
    record(f"status after the push: changed={between['changed']}, {between['last_sync_ref']} -> {between['latest_ref']}")
    record(f"second sync: {second['detail']}; {second['configs'][0]['trace_links']} links, "
           f"+{second['configs'][0]['added']} -{second['configs'][0]['removed']}")
    record(f"graph: {graph['summary']}; link targets are in: {targets}")
    record(f"status after the second sync: changed={after['changed']}")

    assert connected.status_code == 201
    assert sources == {("requirements", "upload"): True, ("code", "upload"): False, ("code", "github"): True}
    assert before["changed"] and before["latest_ref"] == "commit-1" and before["checkable"]
    assert first["version_number"] == 2 and first["configs"][0]["trace_links"] == 3
    assert between["changed"] and (between["last_sync_ref"], between["latest_ref"]) == ("commit-1", "commit-2")
    assert second["version_number"] == 3 and second["configs"][0]["error"] is None
    assert graph["summary"]["links_active"] == 3
    assert graph["summary"]["links_broken"] == 0 and graph["summary"]["nodes_gone"] == 0
    assert targets == ["Loans.java", "security/Auth.java", "security/Auth.java"]
    assert not after["changed"] and after["last_sync_ref"] == "commit-2"


@case(
    id="I-16",
    feature="GitHub OAuth / connecting an account",
    level="integration",
    priority="High",
    why="The callback attaches a GitHub token to a user and then redirects the browser. Both the stored token and the redirect target have to be safe.",
    preconditions="GitHub's token exchange is replaced by a fake that returns a token and the login 'octocat'",
    input="Start OAuth from /app/history, follow the callback; then a callback with a forged state; then start with return_to=//evil.example",
    expected="The browser returns to /app/history with github=connected. The token is stored encrypted and the connection reads connected as octocat. A forged state redirects to the profile page with an error. An outside return address is replaced by the profile page",
)
def test_oauth_connects_the_account_and_never_redirects_off_site(client, db, github, record):
    """Connecting GitHub stores the token encrypted and only ever sends the browser back into the app."""
    headers = sign_up(client)

    location = connect_account(client, headers)
    user = user_of(db, client, headers)
    connection = get(client, headers, "/github/connection")
    record(f"redirect after connecting: {location}")
    record(f"stored token is plain text: {user.github_token == 'gho_token_for_abc'}; decrypts to the token: "
           f"{secrets.decrypt(user.github_token) == 'gho_token_for_abc'}")
    record(f"connection: {connection}")

    forged = client.get("/github/oauth/callback", params={"code": "abc", "state": "not-a-state"},
                        follow_redirects=False).headers["location"]
    outside = connect_account(client, headers, return_to="//evil.example/steal")
    record(f"forged state redirects to: {forged}")
    record(f"return_to=//evil.example redirects to: {outside}")

    home = settings.frontend_url
    assert location.startswith(f"{home}/app/history?github=connected") and "login=octocat" in location
    assert user.github_token != "gho_token_for_abc" and secrets.decrypt(user.github_token) == "gho_token_for_abc"
    assert connection == {"connected": True, "login": "octocat", "configured": True}
    assert forged.startswith(f"{home}/app/profile?github=error")
    assert outside.startswith(f"{home}/app/profile?github=connected")
    assert "evil.example" not in outside


@case(
    id="I-15",
    feature="GitHub connection / a credential GitHub has rejected",
    level="integration",
    priority="High",
    why="A dead token left in place makes the app say 'connected' while every sync fails. The app must notice, clear it, and offer to reconnect.",
    preconditions="A user with a connected GitHub account and a project whose code source is a GitHub repository",
    input="GitHub starts rejecting the token. Read the sync status, then the connection",
    expected="The code source reports an error with needs_reconnect=true and is not marked changed. The stored account token is cleared, and the connection reads not connected",
)
def test_rejected_credential_is_cleared_and_asks_to_reconnect(client, db, github, record):
    """When GitHub rejects the stored token, the app drops it and says the account must be reconnected."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    connect_account(client, headers)
    github.push("commit-1", CODE[2])
    client.post(f"/projects/{project}/sources/github", headers=headers,
                json={"kind": "code", "repository": "owner/library"})
    assert get(client, headers, "/github/connection")["connected"]

    github.credential_dead = True
    status = code_status(client, headers, project)
    token_after = user_of(db, client, headers).github_token
    connection = get(client, headers, "/github/connection")
    record(f"code source status: changed={status['changed']}, needs_reconnect={status['needs_reconnect']}, error={status['error']}")
    record(f"account token still stored: {token_after is not None}")
    record(f"connection: {connection}")

    assert status["error"] and status["needs_reconnect"] and not status["changed"]
    assert token_after is None
    assert connection["connected"] is False and connection["login"] is None
