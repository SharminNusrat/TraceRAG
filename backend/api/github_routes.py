"""Connecting a user's GitHub account: OAuth, and what the connection can reach."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from api.schemas import (
    GitHubConnectionResponse,
    OAuthStartResponse,
    RepositoryLookupRequest,
    RepositoryOption,
)
from core.auth import get_current_user
from core.db.models import User
from core.db.session import get_db
from config import settings
from core import secrets
from core.secrets import SecretError
from core.git import (
    DEFAULT_RETURN,
    GitHubCredentialError,
    GitHubError,
    GitHubRepository,
    authorize_url,
    exchange_code,
    list_repositories,
    parse_repository,
    user_from_state,
    verify_token,
)
from core.sync import forget_account_token

router = APIRouter()
logger = logging.getLogger(__name__)


def oauth_redirect_uri(request: Request) -> str:
    """Where GitHub sends the browser back to. Must match the OAuth app exactly."""
    return str(request.url_for("github_oauth_callback"))


def user_github_token(user: User) -> str | None:
    """This user's own GitHub credential, if they have connected one."""
    return secrets.decrypt(user.github_token) if user.github_token else None


@router.get("/github/connection", response_model=GitHubConnectionResponse)
def github_connection(user: User = Depends(get_current_user)):
    """Whether this user's GitHub connection actually works.

    Asked of GitHub rather than answered from the database. A stored token says
    a connection was made once, not that it still stands - GitHub can end one
    without telling anybody, and reporting "connected" from the presence of a
    dead token is how someone ends up staring at a working-looking connection
    that fails every request.
    """
    rejected = False
    if user.github_token:
        try:
            verify_token(secrets.decrypt(user.github_token))
        except GitHubCredentialError:
            rejected = forget_account_token(user)
        except (GitHubError, SecretError) as error:
            # GitHub unreachable, or the token unreadable. Neither says the
            # connection is over, so it is left alone and reported as it stands.
            logger.warning(f"Could not verify GitHub for user {user.user_id}: {error}")

    return GitHubConnectionResponse(
        connected=bool(user.github_token),
        login=user.github_login or None,
        configured=settings.github_oauth_configured,
        needs_reconnect=rejected,
    )


@router.get("/github/oauth/start", response_model=OAuthStartResponse)
def github_oauth_start(
    request: Request,
    # Where the browser should land afterwards. Sent by whichever screen asked,
    # so reconnecting from a project returns to that project rather than
    # stranding the user on a settings page they never meant to visit.
    return_to: str | None = Query(default=None),
    user: User = Depends(get_current_user),
):
    """Where to send the browser so this user can approve access."""
    if not secrets.available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Connecting GitHub needs secret_key set in backend/.env, so the "
                "token can be stored safely and read back after a restart."
            ),
        )
    try:
        return OAuthStartResponse(
            authorize_url=authorize_url(
                user.user_id, oauth_redirect_uri(request), return_to
            )
        )
    except GitHubError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get("/github/oauth/callback", name="github_oauth_callback")
def github_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Where GitHub sends the browser once the user has answered.

    Reached by a redirect, not by the app, so it cannot return JSON to anyone -
    it stores the result and sends the browser back where it came from, saying
    what happened in the address.
    """
    # Where to land. Only known once the state has been read, so a failure
    # before that falls back to the default rather than guessing.
    destination = DEFAULT_RETURN

    def home(**params) -> RedirectResponse:
        separator = "&" if "?" in destination else "?"
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}{destination}{separator}{urlencode(params)}"
        )

    if error_description:
        # The user pressed Cancel, or GitHub refused.
        return home(github="error", message=error_description)
    if not code or not state:
        return home(github="error", message="GitHub did not complete the sign-in.")

    try:
        # The state says whose account this belongs to, that we started it, and
        # where the person was when they did.
        user_id, destination = user_from_state(state)
        token, login = exchange_code(code, oauth_redirect_uri(request))
    except GitHubError as error:
        return home(github="error", message=str(error))

    user = db.get(User, user_id)
    if user is None:
        return home(github="error", message="That account no longer exists.")

    user.github_token = secrets.encrypt(token)
    user.github_login = login or None
    db.commit()

    logger.info(f"User {user_id} connected GitHub account '{login}'")
    return home(github="connected", login=login or "")


@router.delete("/github/connection", response_model=GitHubConnectionResponse)
def disconnect_github(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Forget this user's GitHub credential.

    Only removes our copy. Whether GitHub still lists TraceRAG as authorised is
    the user's to settle there, and the response says so.
    """
    user.github_token = None
    user.github_login = None
    db.commit()
    return GitHubConnectionResponse(
        connected=False, login=None, configured=settings.github_oauth_configured
    )


@router.get("/github/repositories", response_model=list[RepositoryOption])
def github_repositories(user: User = Depends(get_current_user)):
    """The repositories this user's connection can reach."""
    token = user_github_token(user)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your GitHub account first.",
        )
    try:
        return [RepositoryOption(**repo) for repo in list_repositories(token)]
    except GitHubError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.post("/github/branches", response_model=list[str])
def github_branches(
    request: RepositoryLookupRequest,
    user: User = Depends(get_current_user),
):
    """The branches of a repository, so one can be chosen before connecting."""
    try:
        return GitHubRepository(
            parse_repository(request.repository),
            # A token typed in for this one repository wins; otherwise whatever
            # this user connected their account with.
            token=request.token or user_github_token(user),
        ).branches()
    except GitHubError as error:
        # These messages are written to be read by whoever typed the name in.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))
