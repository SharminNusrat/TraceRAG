"""Letting a person connect their own GitHub account.

The alternative was asking everyone to make a personal access token by hand and
paste it in. This is better for two reasons that matter: the user grants access
themselves and can withdraw it from GitHub at any time, and TraceRAG never has
to hold a credential wider than what they agreed to.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
import requests

from config import settings
from core.auth.security import ALGORITHM, SECRET_KEY
from core.git.repository import (
    API_ROOT, API_VERSION, TIMEOUT_SECONDS, GitHubCredentialError, GitHubError,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"

# Read-only, and only what is needed: contents to fetch files, metadata to list
# repositories and branches. Not write, not delete, not workflows.
SCOPES = "repo:status read:org repo"

# Long enough to sign in to GitHub and click Authorize, short enough that a
# link left lying around stops working.
STATE_MINUTES = 10
# Marks this token as an OAuth handshake, so a login token cannot be presented
# as one, or the other way round.
STATE_PURPOSE = "github-oauth"

# Where the browser lands when nobody said where they came from.
DEFAULT_RETURN = "/app/profile"


def safe_return_path(value: str | None) -> str:
    """A path inside the app to come back to, or the default.

    Only ever a path, never a whole address. The value survives a round trip
    through GitHub and comes back as somewhere to send a browser, so anything
    that could name another host would turn this into an open redirect - a
    link that looks like ours and lands somewhere else.
    """
    if not value or not value.startswith("/"):
        return DEFAULT_RETURN
    # "//host" is protocol-relative and "/\host" is treated the same way by
    # some browsers. Both leave the site despite starting with a slash.
    if value.startswith("//") or value.startswith("/\\"):
        return DEFAULT_RETURN
    return value


def authorize_url(user_id: int, redirect_uri: str, return_to: str | None = None) -> str:
    """Where to send the browser so this user can approve access."""
    if not settings.github_oauth_configured:
        raise GitHubError(
            "GitHub sign-in is not set up on this server. Ask whoever runs it "
            "to add github_client_id and github_client_secret."
        )
    query = urlencode({
        "client_id": settings.github_client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        # Carries who started this, signed, so the callback knows whose account
        # to attach the answer to without keeping server-side state.
        "state": _sign_state(user_id, safe_return_path(return_to)),
        "allow_signup": "false",
    })
    return f"{AUTHORIZE_URL}?{query}"


def user_from_state(state: str) -> tuple[int, str]:
    """Who started the handshake, and where they were when they started it."""
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != STATE_PURPOSE:
            raise GitHubError("That sign-in link is not valid.")
        # Checked again on the way out, not only on the way in: this value is
        # about to become a redirect, and it is signed by us but shaped by
        # whatever asked for the link.
        return int(payload["sub"]), safe_return_path(payload.get("to"))
    except jwt.ExpiredSignatureError:
        raise GitHubError("That sign-in link has expired. Try connecting again.")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        # A state we did not sign. Either tampered with, or a stale link from a
        # server whose secret has since changed.
        raise GitHubError("That sign-in link is not valid.")


def exchange_code(code: str, redirect_uri: str) -> tuple[str, str]:
    """Trade the one-time code for a token. Returns the token and the username."""
    try:
        response = requests.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise GitHubError(f"Could not reach GitHub: {error}") from error

    if not response.ok:
        raise GitHubError(f"GitHub refused the sign-in ({response.status_code}).")

    body = response.json()
    token = body.get("access_token")
    if not token:
        # GitHub answers 200 with an error field for a spent or wrong code.
        raise GitHubError(
            body.get("error_description") or "GitHub did not return an access token."
        )

    return token, _login_for(token)


def verify_token(token: str) -> str:
    """Ask GitHub whether this credential still works. Returns the username.

    The only way to know. A stored token tells you a connection was made once,
    not that it still stands - GitHub can end one at any time, and says so
    nowhere except in the answer to a request.

    Raises GitHubCredentialError when the token is dead. Anything else - a
    network fault, GitHub being down - is not the token's fault and leaves the
    connection alone rather than throwing away a credential that may be fine.
    """
    try:
        response = requests.get(
            f"{API_ROOT}/user",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "Authorization": f"Bearer {token}",
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise GitHubError(f"Could not reach GitHub: {error}") from error

    if response.status_code == 401:
        raise GitHubCredentialError(
            "GitHub rejected the credential. The connection has expired or "
            "been revoked, so it needs granting again."
        )
    if not response.ok:
        raise GitHubError(f"GitHub returned {response.status_code}.")
    return response.json().get("login", "")


def _login_for(token: str) -> str:
    """The username behind a token, so the app can say whose account it is."""
    try:
        response = requests.get(
            f"{API_ROOT}/user",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "Authorization": f"Bearer {token}",
            },
            timeout=TIMEOUT_SECONDS,
        )
        return response.json().get("login", "") if response.ok else ""
    except requests.RequestException:
        # A name is a nicety. Failing to read it must not fail the connection.
        return ""


def list_repositories(token: str, limit: int = 100) -> list[dict]:
    """The repositories this token can reach, most recently pushed first."""
    try:
        response = requests.get(
            f"{API_ROOT}/user/repos",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "Authorization": f"Bearer {token}",
            },
            params={"per_page": limit, "sort": "pushed", "affiliation": "owner,collaborator,organization_member"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise GitHubError(f"Could not reach GitHub: {error}") from error

    if not response.ok:
        raise GitHubError(
            "Could not list your repositories. The connection may have been "
            "revoked on GitHub - try connecting again."
        )

    return [
        {
            "full_name": repo["full_name"],
            "private": repo.get("private", False),
            "default_branch": repo.get("default_branch") or "main",
            "description": repo.get("description"),
        }
        for repo in response.json()
        if repo.get("full_name")
    ]


def _sign_state(user_id: int, return_to: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": STATE_PURPOSE,
            # Signed rather than passed as a query parameter, so it cannot be
            # swapped for another destination on the way through GitHub.
            "to": return_to,
            "iat": now,
            "exp": now + timedelta(minutes=STATE_MINUTES),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
