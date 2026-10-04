"""Asking a source what it is now, and getting its contents.

Two separate questions on purpose. Checking is cheap - one small request per
side - and is what a user is shown before deciding to update. Fetching costs a
download, and is only worth doing for a side that actually moved.
"""

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
    # What the side's files are at the analysis's newest version: a commit, or
    # a fingerprint of an upload. None before anything was stored.
    last_ref: str | None
    # What the side is at its origin right now. None when there is nothing to
    # ask - an uploaded folder has no address to check - or when asking failed.
    latest_ref: str | None
    changed: bool
    # Why it could not be checked, when that is the reason latest_ref is None.
    error: str | None = None
    # Whether the fix is to grant GitHub access again. Distinguishes a dead
    # connection, which one button mends, from a repository problem, which no
    # amount of reconnecting will.
    needs_reconnect: bool = False


def owner_of(source: ProjectSource) -> User | None:
    """The account a side's analysis belongs to."""
    return source.config.project.user if source.config else None


def token_for(source: ProjectSource) -> str | None:
    """The credential to read this side with, most specific first.

    A token given for this one repository beats the owner's account-wide one,
    because it was chosen deliberately for it. Neither means the repository is
    read anonymously - and the server's own token is never substituted here,
    since it can reach things this user never granted.
    """
    if source.access_token:
        return secrets.decrypt(source.access_token)

    owner = owner_of(source)
    if owner is not None and owner.github_token:
        return secrets.decrypt(owner.github_token)

    return None


def forget_account_token(user: User | None) -> bool:
    """Drop an account credential GitHub has rejected. Says whether it did.

    Keeping it would be worse than useless: everything reading the connection
    asks only whether a token is stored, so a dead one left in place makes the
    app report a working GitHub connection while every request behind it fails.
    Removing it is what lets "connected" mean connected.
    """
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
        # Only the person holding the files knows whether an upload has
        # changed, so it is never reported as moved on its own.
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
        # Nothing is wrong with the repository, so say what is: the credential
        # is dead. A source carrying its own token keeps the account connection
        # out of it - that token is the one that failed, not this one.
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
            # With nothing to authenticate as, GitHub answers 404 for a private
            # repository rather than admitting it exists - so its own message
            # would send the user off checking a branch name that is fine.
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
    """Files that moved between commit `base` and commit `head`.

    Returned as old path -> new path. Without this a renamed file reads as one
    deleted and another created, and every link that pointed at it is reported
    broken - which is what makes an ordinary refactor look like damage.

    Best effort: a repository that cannot answer leaves the update to carry on
    without the mapping.
    """
    try:
        renames = repository_for(source).renames_between(base, head)
    except (GitHubError, SecretError) as error:
        logger.warning(f"Could not read renames for {source.location}: {error}")
        return {}

    if renames:
        logger.info(f"{source.location}: {len(renames)} file(s) renamed since {base[:10]}")
    return renames


def fetch_source(source: ProjectSource, destination: Path) -> str:
    """Write a source's current contents into `destination`.

    Returns the ref those contents are at. The caller records it only once the
    files are safely stored - moving it forward any earlier would leave the
    source claiming to be up to date with work that never finished, and the
    next sync would see nothing to do.
    """
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
