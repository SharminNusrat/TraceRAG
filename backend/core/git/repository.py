"""Read-only access to a GitHub repository.

Enough to answer the two questions a sync asks: what is the branch pointing at
now, and give me the files at that commit. Nothing here writes to GitHub.

The archive endpoint is used rather than `git clone`, so no git binary has to
exist on the machine and no .git directory has to be cleaned up afterwards.
"""

import logging
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

import requests

from config import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
# Not out of date: GitHub names its API versions by the day they were
# published, and this is still the only one it has ever published - send no
# header at all and GitHub selects exactly this. Pinned rather than left to the
# default so that the day a newer version becomes the default, the responses
# read here keep the shape this code was written against until it is updated.
API_VERSION = "2022-11-28"

TIMEOUT_SECONDS = 30
# Deliberately not the upload limit: a repository is fetched, not typed in by
# someone waiting, and a codebase with its tests is bigger than any upload.
MAX_REPO_BYTES = 200 * 1024 * 1024
READ_CHUNK = 1024 * 1024


class GitHubError(RuntimeError):
    """The repository could not be read. The message is safe to show a user."""


class GitHubCredentialError(GitHubError):
    """GitHub rejected the credential itself, whatever it was asked for.

    Separate from its parent because it is the one failure that says something
    about the connection rather than about the repository: nothing else will
    work either until a new credential is granted, so a caller can stop
    treating the stored one as usable.
    """


# Everything GitHub itself puts in front of a user: the address bar, the green
# clone button's HTTPS and SSH forms, and the "owner/name" it writes in prose.
REPOSITORY_PATTERNS = (
    re.compile(r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<name>[^/\s?#]+)"),
    re.compile(r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<name>[^/\s]+)"),
    re.compile(r"^(?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+)$"),
)


def parse_repository(value: str) -> str:
    """Turn whatever the user pasted into "owner/name".

    Asking someone to retype a repository as "owner/name" when they have its
    URL on the clipboard is a needless way to collect typos.
    """
    text = (value or "").strip().rstrip("/")
    for pattern in REPOSITORY_PATTERNS:
        match = pattern.match(text)
        if match:
            # ".git" is part of the clone address, not of the repository name.
            name = match.group("name")
            return f"{match.group('owner')}/{name[:-4] if name.endswith('.git') else name}"

    raise GitHubError(
        f"'{value}' does not look like a GitHub repository. Paste its URL, or "
        f"write it as owner/name."
    )


class GitHubRepository:
    """One repository, addressed as "owner/name"."""

    def __init__(self, full_name: str, token: str | None = None):
        self.full_name = full_name.strip().strip("/")
        self.token = token
        # Whether anyone can read this repository without signing in. Asked at
        # most once, and only when it matters.
        self._public: bool | None = None

    def _effective_token(self) -> str | None:
        """The token this request should be made with.

        The caller's own, when they have one. Otherwise the server's - but
        **only for a repository anyone can already read**, where it does
        nothing except raise the rate limit.

        Never for a private one. The server's token belongs to whoever
        installed TraceRAG, and letting it stand in for a user's would hand
        every signed-in person read access to every private repository that
        token can reach, just by knowing its name.
        """
        if self.token:
            return self.token
        if not settings.github_token:
            return None
        if self._public is None:
            self._public = self._is_public()
        return settings.github_token if self._public else None

    def _is_public(self) -> bool:
        """Can this repository be read with no credentials at all?"""
        try:
            response = requests.get(
                f"{API_ROOT}/repos/{self.full_name}",
                headers={"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": API_VERSION},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            # Unreachable is not the same as private, but the safe reading of
            # "I could not confirm this is public" is to withhold the token.
            return False
        return response.ok

    def head_commit(self, branch: str) -> str:
        """The commit the branch points at now."""
        data = self._get_json(
            f"/repos/{self.full_name}/branches/{branch}",
            missing=(
                f"Branch '{branch}' was not found in '{self.full_name}'. Check the "
                f"branch name, and that the token can read the repository if it "
                f"is private."
            ),
        )
        sha = (data.get("commit") or {}).get("sha")
        if not sha:
            raise GitHubError(f"Branch '{branch}' has no commit in {self.full_name}.")
        return sha

    def branches(self) -> list[str]:
        """Branch names, so a project can be pointed at one of them."""
        data = self._get_json(f"/repos/{self.full_name}/branches", params={"per_page": 100})
        return [branch["name"] for branch in data if branch.get("name")]

    def default_branch(self) -> str:
        return self._get_json(f"/repos/{self.full_name}").get("default_branch") or "main"

    def changed_files(self, base: str, head: str) -> list[dict]:
        """What happened to each file between two commits.

        Each entry carries a `status` - added, modified, removed, renamed - and
        a renamed one also carries the name it had before. That last part is
        the valuable bit: a moved or renamed file is otherwise indistinguishable
        from one deleted and another created, which makes every link on it look
        broken when nothing was actually lost.
        """
        data = self._get_json(
            f"/repos/{self.full_name}/compare/{base}...{head}",
            # 300 files is the endpoint's ceiling. Beyond that the list comes
            # back truncated rather than paginated, and a diff that large is
            # past the point where knowing each rename would save much.
            params={"per_page": 300},
            missing=f"Could not compare {base[:8]}…{head[:8]} in '{self.full_name}'.",
        )
        return [
            {
                "filename": entry.get("filename"),
                "status": entry.get("status"),
                "previous_filename": entry.get("previous_filename"),
            }
            for entry in (data.get("files") or [])
            if entry.get("filename")
        ]

    def renames_between(self, base: str, head: str) -> dict[str, str]:
        """Files that moved, as old path -> new path."""
        return {
            entry["previous_filename"]: entry["filename"]
            for entry in self.changed_files(base, head)
            if entry["status"] == "renamed" and entry["previous_filename"]
        }

    def download(self, ref: str, destination: Path) -> None:
        """Write the repository at `ref` into `destination`."""
        destination.mkdir(parents=True, exist_ok=True)
        archive = self._fetch_archive(ref)
        try:
            self._extract(archive, destination)
        finally:
            archive.unlink(missing_ok=True)

    # ----- HTTP -----

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        token = self._effective_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_json(self, path: str, params: dict | None = None, missing: str | None = None):
        try:
            response = requests.get(
                f"{API_ROOT}{path}",
                headers=self._headers(),
                params=params,
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise GitHubError(f"Could not reach GitHub: {error}") from error

        self._raise_for_status(response, missing)
        return response.json()

    def _raise_for_status(self, response, missing: str | None = None) -> None:
        if response.ok:
            return
        if response.status_code == 404:
            # A branch that does not exist and a repository the token cannot
            # see both answer 404, so the caller says which it asked for and
            # neither message promises more than GitHub actually told us.
            raise GitHubError(missing or (
                f"'{self.full_name}' was not found. Check the name, and that the "
                f"token can read it if the repository is private."
            ))
        if response.status_code == 401:
            # The credential itself was rejected, whatever it was asked for.
            # Naming the repository here sends people to check its permissions
            # when nothing about the repository is wrong.
            raise GitHubCredentialError(
                "GitHub rejected the credential. The connection has expired or "
                "been revoked, so it needs granting again."
            )
        if response.status_code == 403:
            # Two very different things share this code, and the remaining quota
            # is what tells them apart.
            if response.headers.get("x-ratelimit-remaining") == "0":
                raise GitHubError(
                    "GitHub's rate limit has been reached. It resets within the "
                    "hour; connecting your GitHub account raises the limit."
                )
            raise GitHubError(
                f"The credential is valid but not allowed to read "
                f"'{self.full_name}'. Check that the account or token it belongs "
                f"to has access to that repository."
            )
        raise GitHubError(
            f"GitHub returned {response.status_code} for '{self.full_name}'."
        )

    def _fetch_archive(self, ref: str) -> Path:
        """Stream the tarball to a temporary file. Caller deletes it."""
        # To disk rather than memory: a repository is not a size worth holding
        # in the process, and tarfile wants a file it can seek in anyway.
        handle = tempfile.NamedTemporaryFile(prefix="tracerag-repo-", suffix=".tar.gz", delete=False)
        path = Path(handle.name)
        try:
            # The handle is closed on the way out of this block, before any
            # except clause below runs: Windows will not delete a file that is
            # still open, so a failed download could not clean up after itself.
            with handle:
                written = 0
                with requests.get(
                    f"{API_ROOT}/repos/{self.full_name}/tarball/{ref}",
                    headers=self._headers(),
                    stream=True,
                    timeout=TIMEOUT_SECONDS,
                ) as response:
                    self._raise_for_status(response, missing=(
                        f"'{ref}' was not found in '{self.full_name}'."
                    ))
                    for chunk in response.iter_content(READ_CHUNK):
                        written += len(chunk)
                        if written > MAX_REPO_BYTES:
                            raise GitHubError(
                                f"'{self.full_name}' is larger than the "
                                f"{MAX_REPO_BYTES // (1024 * 1024)} MB limit."
                            )
                        handle.write(chunk)
        except requests.RequestException as error:
            path.unlink(missing_ok=True)
            raise GitHubError(f"Could not download {self.full_name}: {error}") from error
        except Exception:
            path.unlink(missing_ok=True)
            raise

        return path

    def _extract(self, archive: Path, destination: Path) -> None:
        """Unpack the tarball, dropping the commit-named folder GitHub wraps it in."""
        resolved = destination.resolve()
        total = 0
        files = 0

        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                # Directories are made as needed; anything that is not a plain
                # file - a symlink, a device node - is not source code and is
                # the shape an archive uses to escape the directory it is
                # unpacked into.
                if not member.isfile():
                    continue

                relative = member.name.split("/", 1)
                if len(relative) < 2 or not relative[1]:
                    continue

                target = (destination / relative[1]).resolve()
                if not target.is_relative_to(resolved):
                    logger.warning(f"Skipping archive entry outside the target: {member.name}")
                    continue

                total += member.size
                if total > MAX_REPO_BYTES:
                    raise GitHubError(
                        f"'{self.full_name}' unpacks to more than the "
                        f"{MAX_REPO_BYTES // (1024 * 1024)} MB limit."
                    )

                source = tar.extractfile(member)
                if source is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink)
                files += 1

        logger.info(f"Extracted {files} file(s) from {self.full_name} into {destination}")
