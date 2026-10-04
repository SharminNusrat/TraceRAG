"""GitHub as a source, and the account connection - against a pretend GitHub, never the real one."""

from urllib.parse import parse_qs, urlparse

from config import settings
from core import secrets
from core.db.models import User
from tests.helpers import (
    CODE, analyse_and_save, analysis_path, get, new_project, report, side_ids, sign_up, sync,
)
from tests.recorder import case


def code_status(client, headers, project, config) -> dict:
    """Where the code side stands, as the Update dialog is told."""
    rows = get(client, headers, f"{analysis_path(project, config)}/sync/status")
    return next(row for row in rows if row["kind"] == "code")


def connect_code_side(client, headers, project, config, repository="owner/library"):
    """Take the analysis's code side from the pretend repository."""
    code = side_ids(client, headers, project, config)["target"]
    return client.post(f"{analysis_path(project, config)}/sources/{code}/github", headers=headers,
                       json={"repository": repository}), code


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
    feature="GitHub update / fetch, version and follow renames",
    level="integration",
    priority="Critical",
    why="The demo updates code from GitHub. A new commit must be fetched and versioned, and a moved file must keep its links instead of breaking them.",
    preconditions="A saved requirements->code analysis from uploaded files. GitHub is replaced by a fake repository holding the same code at commit-1",
    input="Take the analysis's code side from the repository and update it. Then push commit-2, which moves Auth.java to security/Auth.java, and update again",
    expected="The code side becomes a GitHub side; the requirements side stays an upload. First update: commit-1 is fetched, holds exactly the stored files, so nothing changed and no version is made - and commit-1 is no longer reported as new. Second update: commit-2 fetched, Auth.java reported renamed, version 2; the report lists the same 3 links as valid, now on security/Auth.java, and none broken or lost. Afterwards the side reports no change",
)
def test_github_side_is_fetched_versioned_and_renames_followed(client, github, record):
    """A code side taken from GitHub is fetched when it moves, and a renamed file keeps its links."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    github.push("commit-1", CODE[2])

    connected, code = connect_code_side(client, headers, project, config, "https://github.com/owner/library")
    sides = {s["role"]: (s["kind"], s["origin"]) for s in get(client, headers, f"{analysis_path(project, config)}/sources")}
    before = code_status(client, headers, project, config)
    record(f"connect: {connected.status_code}, location {connected.json()['location']}, branch {connected.json()['branch']}")
    record(f"sides (kind, origin): {sides}")
    record(f"status before the first update: changed={before['changed']}, {before['last_sync_ref']} -> {before['latest_ref']}")

    first = sync(client, headers, project, config, source_id=code)["job"]["result"]
    seen = code_status(client, headers, project, config)
    record(f"first update: synced={first['synced']}, {first['detail']}")
    record(f"status after the first update: changed={seen['changed']}, at {seen['last_sync_ref']}")

    moved = {"security/Auth.java": CODE[2]["Auth.java"], "Loans.java": CODE[2]["Loans.java"]}
    github.push("commit-2", moved, renames={"Auth.java": "security/Auth.java"})
    between = code_status(client, headers, project, config)
    second = sync(client, headers, project, config, source_id=code)["job"]["result"]
    links = report(client, headers, project, config)
    after = code_status(client, headers, project, config)
    targets = sorted(link["target_id"].split("::")[0] for link in links["links"])
    record(f"status after the push: changed={between['changed']}, {between['last_sync_ref']} -> {between['latest_ref']}")
    record(f"second update: {second['detail']}; {second['trace_links']} links, +{second['added']} -{second['removed']}")
    record(f"files renamed: {second['changes']['files']['renamed']}")
    record(f"report v1 -> v2: {links['summary']}; link targets are in: {targets}")
    record(f"status after the second update: changed={after['changed']}")

    assert connected.status_code == 200
    assert sides == {"source": ("requirements", "upload"), "target": ("code", "github")}
    assert before["changed"] and before["latest_ref"] == "commit-1" and before["checkable"]
    assert first["synced"] is False and "Nothing has changed" in first["detail"]
    assert not seen["changed"] and seen["last_sync_ref"] == "commit-1"
    assert between["changed"] and (between["last_sync_ref"], between["latest_ref"]) == ("commit-1", "commit-2")
    assert second["version_number"] == 2
    assert second["changes"]["files"]["renamed"] == [{"old": "Auth.java", "new": "security/Auth.java"}]
    assert links["summary"] == {"valid": 3, "no_longer_found": 0, "broken": 0, "new": 0, "uncovered": 1}
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
    assert connection == {"connected": True, "login": "octocat", "configured": True, "needs_reconnect": False}
    assert forged.startswith(f"{home}/app/profile?github=error")
    assert outside.startswith(f"{home}/app/profile?github=connected")
    assert "evil.example" not in outside


@case(
    id="I-15",
    feature="GitHub connection / a credential GitHub has rejected",
    level="integration",
    priority="High",
    why="A dead token left in place makes the app say 'connected' while every sync fails. The app must notice, clear it, and offer to reconnect.",
    preconditions="A user with a connected GitHub account and an analysis whose code side is a GitHub repository",
    input="GitHub starts rejecting the token. Read the update status, then the connection",
    expected="The code side reports an error with needs_reconnect=true and is not marked changed. The stored account token is cleared, and the connection reads not connected",
)
def test_rejected_credential_is_cleared_and_asks_to_reconnect(client, db, github, record):
    """When GitHub rejects the stored token, the app drops it and says the account must be reconnected."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    connect_account(client, headers)
    github.push("commit-1", CODE[2])
    connect_code_side(client, headers, project, config)
    assert get(client, headers, "/github/connection")["connected"]

    github.credential_dead = True
    status = code_status(client, headers, project, config)
    token_after = user_of(db, client, headers).github_token
    connection = get(client, headers, "/github/connection")
    record(f"code side status: changed={status['changed']}, needs_reconnect={status['needs_reconnect']}, error={status['error']}")
    record(f"account token still stored: {token_after is not None}")
    record(f"connection: {connection}")

    assert status["error"] and status["needs_reconnect"] and not status["changed"]
    assert token_after is None
    assert connection["connected"] is False and connection["login"] is None


@case(
    id="I-41",
    feature="GitHub connection / asking to reconnect",
    level="integration",
    priority="High",
    why="The Connect dialog offers 'Reconnect GitHub' instead of the form when the account connection has died. That only works if the connection check says so.",
    preconditions="A user who connected their GitHub account",
    input="GitHub starts rejecting the token. Read the connection twice",
    expected="First read: not connected, needs_reconnect=true, and the dead token is cleared. Second read: not connected, needs_reconnect=false - there is nothing stored left to reject",
)
def test_connection_says_when_it_needs_reconnecting(client, db, github, record):
    """The connection check reports a token GitHub has just rejected as needing a reconnect."""
    headers = sign_up(client)
    connect_account(client, headers)
    github.credential_dead = True

    first = get(client, headers, "/github/connection")
    second = get(client, headers, "/github/connection")
    record(f"first read: {first}")
    record(f"second read: {second}")

    assert (first["connected"], first["needs_reconnect"]) == (False, True)
    assert user_of(db, client, headers).github_token is None
    assert (second["connected"], second["needs_reconnect"]) == (False, False)


@case(
    id="I-42",
    feature="GitHub side / whose credential a fetch uses",
    level="integration",
    priority="Critical",
    why="With the account connected, a code side is taken from GitHub without a token of its own, and every fetch must use the account connection. A side given its own token on purpose must keep using that one.",
    preconditions="A user who connected their GitHub account (token gho_token_for_abc), with two saved analyses",
    input="Analysis A: take the code side from the repository with no token, and update it. Analysis B: take it with its own token 'side-token', and update it",
    expected="A's side stores no token (has_token=false) and its fetch used the account token. B's side stores its token (has_token=true) and its fetch used 'side-token'",
)
def test_fetch_uses_the_account_unless_the_side_has_its_own_token(client, github, record):
    """A side without a token is fetched with the account connection; a side with one uses its own."""
    headers = sign_up(client)
    project = new_project(client, headers)
    connect_account(client, headers)
    account = analyse_and_save(client, headers, project)["config_id"]
    own = analyse_and_save(client, headers, project)["config_id"]
    github.push("commit-1", {**CODE[2], "Extra.java": "public class Extra { void audit() {} }\n"})

    without, code = connect_code_side(client, headers, project, account)
    sync(client, headers, project, account, source_id=code)
    with_token = client.post(
        f"{analysis_path(project, own)}/sources/{side_ids(client, headers, project, own)['target']}/github",
        headers=headers, json={"repository": "owner/library", "token": "side-token"},
    )
    sync(client, headers, project, own, source_id=with_token.json()["source_id"])
    record(f"account side: has_token={without.json()['has_token']}; own-token side: has_token={with_token.json()['has_token']}")
    record(f"tokens the two fetches used: {github.download_tokens}")

    assert without.json()["has_token"] is False and with_token.json()["has_token"] is True
    assert github.download_tokens == ["gho_token_for_abc", "side-token"]
