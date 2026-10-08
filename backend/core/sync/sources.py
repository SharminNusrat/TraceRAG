"""Asking a source what it is now, and getting its contents."""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, object_session

from core import secrets
from core.db.models import ProjectConfig, ProjectSource, User
from core.git import GitHubCredentialError, GitHubError, GitHubRepository
from core.projects.service import ORIGIN_GITHUB, latest_artifact, list_sides
from core.secrets import SecretError

logger = logging.getLogger(__name__)


class SyncError(RuntimeError):
    """A source could not be synced. The message is safe to show a user."""


@dataclass
class SourceStatus:
    """Where one source stands against the place it comes from."""

    source: ProjectSource
    last_ref: str | None
    latest_ref: str | None
    changed: bool
    error: str | None = None
    needs_reconnect: bool = False


def owner_of(source: ProjectSource) -> User | None:
    """The account a side's analysis belongs to."""
    return source.config.project.user if source.config else None


def token_for(source: ProjectSource) -> str | None:
    """The credential to read this side with, most specific first."""
    if source.access_token:
        return secrets.decrypt(source.access_token)

    owner = owner_of(source)
    if owner is not None and owner.github_token:
        return secrets.decrypt(owner.github_token)

    return None


def forget_account_token(user: User | None) -> bool:
    """Drop an account credential GitHub has rejected. Says whether it did."""

    if user is None or not user.github_token:
        return False

    user.github_token = None
    user.github_login = None
    session = object_session(user)
    if session is not None:
        session.commit()

    logger.warning(
        f"Cleared the GitHub connection for user {user.user_id}: GitHub rejected it"
    )
    return True


def repository_for(source: ProjectSource) -> GitHubRepository:
    """The repository a source points at, opened with whatever can read it."""
    return GitHubRepository(source.location, token=token_for(source))


def check_source(source: ProjectSource, last_ref: str | None) -> SourceStatus:
    """Whether one side has moved since `last_ref`, what it last took in."""
    if source.origin != ORIGIN_GITHUB:
        return SourceStatus(source=source, last_ref=last_ref, latest_ref=None, changed=False)

    try:
        token = token_for(source)
    except SecretError as error:
        logger.warning(f"Could not read the credential for {source.location}: {error}")
        return SourceStatus(
            source=source, last_ref=last_ref, latest_ref=None, changed=False, error=str(error),
        )

    try:
        repository = GitHubRepository(source.location, token=token)
        latest = repository.head_commit(source.branch or repository.default_branch())
    except GitHubCredentialError as error:
        if not source.access_token:
            forget_account_token(owner_of(source))
        logger.warning(f"Could not check {source.location}: {error}")
        return SourceStatus(
            source=source, last_ref=last_ref, latest_ref=None, changed=False,
            error=str(error), needs_reconnect=not source.access_token,
        )
    except GitHubError as error:
        logger.warning(f"Could not check {source.location}: {error}")
        if not token:
            return SourceStatus(
                source=source, last_ref=last_ref, latest_ref=None, changed=False,
                needs_reconnect=True,
                error=(
                    f"'{source.location}' could not be read, and there is no GitHub "
                    f"connection to read it with. Reconnect your GitHub account, or "
                    f"check the repository and branch names if it is public."
                ),
            )
        # One unreachable repository must not stop the others being reported.
        return SourceStatus(
            source=source, last_ref=last_ref, latest_ref=None, changed=False, error=str(error),
        )

    return SourceStatus(
        source=source,
        last_ref=last_ref,
        latest_ref=latest,
        # Never fetched before counts as changed: nothing here came from there.
        changed=latest != last_ref,
    )


def check_sides(db: Session, config: ProjectConfig) -> list[SourceStatus]:
    """Where both sides of an analysis stand."""
    return [check_source(side, last_ref(db, side)) for side in list_sides(db, config)]


def last_ref(db: Session, side: ProjectSource) -> str | None:
    """What a side was last seen at: the newest commit an update fetched from
    it, or failing that, what its stored files are."""
    if side.checked_ref:
        return side.checked_ref
    held = latest_artifact(db, side)
    return held.ref if held else None


def renames_since(source: ProjectSource, base: str, head: str) -> dict[str, str]:
    """Files that moved between commit `base` and commit `head`."""
    try:
        renames = repository_for(source).renames_between(base, head)
    except (GitHubError, SecretError) as error:
        logger.warning(f"Could not read renames for {source.location}: {error}")
        return {}

    if renames:
        logger.info(f"{source.location}: {len(renames)} file(s) renamed since {base[:10]}")
    return renames


def fetch_source(source: ProjectSource, destination: Path) -> str:
    """Write a source's current contents into `destination`."""
    if source.origin != ORIGIN_GITHUB:
        raise SyncError(
            f"'{source.name}' is an uploaded source. Its files have to be "
            f"provided rather than fetched."
        )

    try:
        repository = repository_for(source)
        ref = source.branch or repository.default_branch()
        commit = repository.head_commit(ref)
        repository.download(commit, destination)
    except (GitHubError, SecretError) as error:
        raise SyncError(str(error)) from error

    logger.info(f"Fetched {source.location}@{ref} at {commit[:10]} into {destination}")
    return commit
