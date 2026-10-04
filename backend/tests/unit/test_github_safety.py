"""The parts of the GitHub connection that guard against misuse. None of it reaches GitHub."""

import io
import tarfile
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from config import settings
from core import secrets
from core.auth.security import create_access_token
from core.git import oauth
from core.git.oauth import DEFAULT_RETURN, safe_return_path, user_from_state
from core.git.repository import GitHubError, GitHubRepository, parse_repository
from core.secrets import SecretError
from tests.recorder import case


def signed(**claims) -> str:
    """A state token as the application signs them, with chosen claims."""
    now = datetime.now(timezone.utc)
    payload = {"sub": "7", "purpose": oauth.STATE_PURPOSE, "to": "/app/history",
               "iat": now, "exp": now + timedelta(minutes=5), **claims}
    return jwt.encode(payload, oauth.SECRET_KEY, algorithm=oauth.ALGORITHM)


@case(
    id="U-21",
    feature="GitHub OAuth / safe_return_path",
    level="unit",
    priority="High",
    why="This value becomes a browser redirect after sign-in. If it can name another site, a link that looks like ours lands on an attacker's.",
    input="'/app/history?project=4', 'https://evil.com', '//evil.com', '/\\\\evil.com', 'app/profile', '' and None",
    expected="Only the first is kept; every other value becomes the default '/app/profile'",
)
def test_return_path_cannot_leave_the_site(record):
    """Only a path inside the app is accepted as somewhere to send the browser back to."""
    values = ["/app/history?project=4", "https://evil.com", "//evil.com", "/\\evil.com", "app/profile", "", None]
    results = {str(value): safe_return_path(value) for value in values}
    record(results)

    assert results.pop("/app/history?project=4") == "/app/history?project=4"
    assert set(results.values()) == {DEFAULT_RETURN}


@case(
    id="U-22",
    feature="GitHub OAuth / state token",
    level="unit",
    priority="High",
    why="The state says whose account a GitHub token gets attached to. A forged or reused one would attach it to someone else.",
    input="A valid state; one with a character changed; one signed for another purpose; an expired one; an ordinary login token used as state",
    expected="The valid one returns (user id, return path); the other four are refused with a GitHubError",
)
def test_state_is_only_accepted_when_we_signed_it_for_this(record):
    """A state token is accepted only if it is ours, unexpired and made for this handshake."""
    user_id, destination = user_from_state(oauth._sign_state(7, "/app/history"))
    record(f"valid state -> user {user_id}, return to {destination}")
    assert (user_id, destination) == (7, "/app/history")

    valid = signed()
    refused = {
        "tampered": valid[:-2] + ("aa" if not valid.endswith("aa") else "bb"),
        "wrong purpose": signed(purpose="something-else"),
        "expired": signed(exp=datetime.now(timezone.utc) - timedelta(minutes=1)),
        "login token": create_access_token(7),
    }
    for name, state in refused.items():
        with pytest.raises(GitHubError) as refusal:
            user_from_state(state)
        record(f"{name} -> {refusal.value}")

    # A signed state cannot smuggle in an outside address either.
    assert user_from_state(signed(to="//evil.com"))[1] == DEFAULT_RETURN


@case(
    id="U-25",
    feature="GitHub sync / archive extraction",
    level="unit",
    priority="High",
    why="A repository tarball is unpacked onto the server. Entries with '..' or symlinks are how an archive escapes its folder.",
    input="A tar.gz shaped like GitHub's: 'repo-abc/src/A.java', 'repo-abc/../../evil.java', a symlink, and a file with no folder",
    expected="Only src/A.java is written, without the 'repo-abc' wrapper folder; nothing outside the destination",
)
def test_repository_archive_cannot_escape_or_link_out(tmp_path, record):
    """Only plain files inside the repository are unpacked, with GitHub's wrapper folder dropped."""
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("repo-abc/src/A.java", "repo-abc/../../evil.java", "README"):
            data = b"class A {}"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("repo-abc/link.java")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../outside.java"
        tar.addfile(link)

    destination = tmp_path / "work" / "checkout"
    GitHubRepository("owner/repo")._extract(archive, destination)

    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    record(f"files on disk after extraction: {written}")

    assert written == ["repo.tar.gz", "work/checkout/src/A.java"]


@case(
    id="U-23",
    feature="GitHub sync / parse_repository",
    level="unit",
    priority="Medium",
    why="Users paste whatever GitHub shows them. Each form must resolve to the same repository, and junk must be refused before any request.",
    input="'owner/repo', 'https://github.com/owner/repo', 'github.com/owner/repo.git', 'git@github.com:owner/repo.git', a URL with a trailing slash and a /tree/main suffix; then 'not a repository!!' and ''",
    expected="Every valid form -> 'owner/repo'; the two invalid values raise GitHubError",
)
def test_repository_names_are_read_from_any_form(record):
    """Every way GitHub writes a repository's address is understood; anything else is refused."""
    forms = [
        "owner/repo",
        "https://github.com/owner/repo",
        "github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
        "https://github.com/owner/repo/",
        "https://github.com/owner/repo/tree/main",
    ]
    parsed = {form: parse_repository(form) for form in forms}
    record(parsed)
    assert set(parsed.values()) == {"owner/repo"}

    for junk in ("not a repository!!", ""):
        with pytest.raises(GitHubError) as refusal:
            parse_repository(junk)
        record(f"{junk!r} -> {refusal.value}")


@case(
    id="U-24",
    feature="Secrets / token encryption",
    level="unit",
    priority="Medium",
    why="GitHub tokens are stored in the database. They must not be readable there, and a changed key must fail loudly, not return garbage.",
    input="Encrypt 'ghp_exampletoken', decrypt it, then decrypt it again after the secret key has changed",
    expected="The stored value does not contain the token; decrypting returns it; with another key, SecretError",
)
def test_tokens_are_encrypted_at_rest(monkeypatch, record):
    """A stored token is unreadable without the key, and says so when the key is wrong."""
    token = "ghp_exampletoken"
    stored = secrets.encrypt(token)
    record(f"stored form starts: {stored[:16]}... ({len(stored)} characters)")

    assert token not in stored
    assert secrets.decrypt(stored) == token

    monkeypatch.setattr(settings, "secret_key", "a-different-secret-key-0123456789abcdef")
    with pytest.raises(SecretError) as refusal:
        secrets.decrypt(stored)
    record(f"with a different key: {refusal.value}")
