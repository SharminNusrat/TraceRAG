"""Stand-ins for the models and for GitHub.

A test must give the same answer every time, cost nothing and need no network,
and a real model offers none of the three. These keep the shape of the real
thing - they subclass the same base classes the pipeline is written against -
and replace only the part that would call out.
"""

import math
import re
import zlib

from core.classification.base import Classifier, Verdict
from core.classification.chat_provider import ChatProvider
from core.embedding.base import EmbeddingCreator
from core.schemas import Element

# Wide enough that two different words rarely land on the same dimension.
DIMENSIONS = 512

# Words that appear in almost every requirement or Java file, and so say
# nothing about whether two elements are related.
STOPWORDS = {
    "public", "private", "class", "return", "string", "boolean", "static",
    "final", "import", "package", "these", "their", "there", "which", "should",
    "shall", "using", "system",
}


def words(text: str) -> list[str]:
    """The words of a text, with camelCase names split into their parts."""
    return [word.lower() for word in re.findall(r"[A-Za-z][a-z]+", text)]


def keywords(text: str) -> set[str]:
    """The words that could tie two elements together."""
    return {word for word in words(text) if len(word) >= 5 and word not in STOPWORDS}


class FakeEmbedder(EmbeddingCreator):
    """A bag-of-words vector: texts that share words come out close together."""

    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        return [self.vector(element.content) for element in elements]

    @staticmethod
    def vector(text: str) -> list[float]:
        counts = [0.0] * DIMENSIONS
        for word in words(text):
            # Short words ("the", "can") are in everything and tie nothing together.
            if len(word) < 4:
                continue
            # crc32 rather than hash(): hash() is salted per process, and the
            # same text must embed the same way on every run.
            counts[zlib.crc32(word.encode("utf-8")) % DIMENSIONS] += 1.0

        length = math.sqrt(sum(value * value for value in counts))
        if length == 0:
            # A text with no words still needs a direction to be compared by.
            return [1.0] + [0.0] * (DIMENSIONS - 1)
        return [value / length for value in counts]


class FakeClassifier(Classifier):
    """Links two elements when they share a keyword.

    Subclasses the real Classifier, so the caching around the model call is the
    application's own and only the call itself is replaced.
    """

    def __init__(self, cache_namespace: str | None = None):
        super().__init__(cache_namespace)
        # Every pair actually asked about, so a test can count the model calls.
        self.asked: list[tuple[str, str]] = []

    def _ask(self, source: Element, target: Element) -> Verdict:
        self.asked.append((source.identifier, target.identifier))
        shared = keywords(source.content) & keywords(target.content)
        if not shared:
            return False, None
        return True, f"Both mention: {', '.join(sorted(shared))}"


def classifier_for(classifier_type, use_cache: bool) -> FakeClassifier:
    """Stands in for pipeline_factory.get_classifier."""
    return FakeClassifier("fake-classifier" if use_cache else None)


class FakeChatProvider(ChatProvider):
    """A chat model that answers a summary request in the format asked for."""

    def __init__(self, reply: str | None = None):
        self.reply = reply
        self.prompts: list[str] = []

    def chat(self, prompt: str, system_message: str = None) -> str:
        self.prompts.append(prompt)
        if self.reply is not None:
            return self.reply
        # The summariser numbers its items "1. [type] identifier" and expects
        # one numbered line back for each.
        numbers = re.findall(r"^(\d+)\. \[", prompt, flags=re.MULTILINE)
        return "\n".join(f"{number}: Summary of item {number}." for number in numbers)

    def model_name(self) -> str:
        return "fake-chat-model"


class FakeGitHub:
    """One pretend repository, standing in for github.com.

    Holds what the real one would answer: which commit the branch is at, the
    files at that commit, and which files were renamed to get there. A test
    changes these to play out a push.
    """

    def __init__(self):
        self.head = "commit-1"
        self.files: dict[str, str] = {}
        self.renames: dict[str, str] = {}
        # When set, every request is refused the way GitHub refuses a dead token.
        self.credential_dead = False
        self.login = "octocat"

    def push(self, commit: str, files: dict[str, str], renames: dict[str, str] | None = None) -> None:
        self.head, self.files, self.renames = commit, files, renames or {}

    def check(self) -> None:
        from core.git import GitHubCredentialError
        if self.credential_dead:
            raise GitHubCredentialError("GitHub rejected the access token.")

    def repository(self, full_name: str, token: str | None = None):
        """What the application gets when it opens a repository."""
        return FakeRepository(self, full_name, token)

    # The account-level calls, patched in beside the repository.
    def verify_token(self, token: str) -> str:
        self.check()
        return self.login

    def exchange_code(self, code: str, redirect_uri: str) -> tuple[str, str]:
        return f"gho_token_for_{code}", self.login

    def list_repositories(self, token: str, limit: int = 100) -> list[dict]:
        self.check()
        return [{"full_name": "owner/library", "private": True, "default_branch": "main"}]


class FakeRepository:
    """The methods of GitHubRepository the application calls, answered from a FakeGitHub."""

    def __init__(self, github: FakeGitHub, full_name: str, token: str | None):
        self.github = github
        self.full_name = full_name
        self.token = token

    def default_branch(self) -> str:
        self.github.check()
        return "main"

    def branches(self) -> list[str]:
        self.github.check()
        return ["main", "develop"]

    def head_commit(self, branch: str) -> str:
        self.github.check()
        return self.github.head

    def renames_between(self, base: str, head: str) -> dict[str, str]:
        return dict(self.github.renames)

    def download(self, ref: str, destination) -> None:
        self.github.check()
        for path, text in self.github.files.items():
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
