"""Persistent classification cache, backed by the application database."""

import logging
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.content import normalise
from core.db.models import ClassificationCacheEntry
from core.db.session import SessionLocal

logger = logging.getLogger(__name__)

Verdict = tuple[bool, str | None]


def namespace_for(model: str, classifier: str, *templates: str) -> str:
    """A namespace that changes whenever the question would change."""
    prompt_hash = sha256("\x00".join(templates).encode("utf-8")).hexdigest()[:8]
    return f"{classifier}:{model}:{prompt_hash}"


def question(source_type: str, source_content: str, target_type: str, target_content: str) -> str:
    """The pair reduced to what decides the model's answer."""
    parts = (source_type, source_content, target_type, target_content)
    return "\x00".join(normalise(part) for part in parts)


class PersistentClassificationCache:

    def __init__(self, namespace: str, session_factory=SessionLocal):
        self.namespace = namespace
        self.session_factory = session_factory

    def get_many(self, questions: list[str]) -> dict[str, Verdict]:
        """The verdicts already stored for these pairs, keyed by question."""
        if not questions:
            return {}

        by_hash = {self._key(text): text for text in questions}
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    ClassificationCacheEntry.pair_hash,
                    ClassificationCacheEntry.linked,
                    ClassificationCacheEntry.explanation,
                ).where(
                    ClassificationCacheEntry.namespace == self.namespace,
                    ClassificationCacheEntry.pair_hash.in_(list(by_hash)),
                )
            ).all()

        return {by_hash[pair_hash]: (linked, explanation) for pair_hash, linked, explanation in rows}

    def set_many(self, verdicts: dict[str, Verdict]) -> None:
        """Store verdicts, overwriting any entry already under that key."""
        # Upsert, not insert: two analyses running at once can ask the same
        # question, and the second must not fail the whole batch.
        if not verdicts:
            return

        rows = [
            {
                "namespace": self.namespace,
                "pair_hash": self._key(text),
                "linked": linked,
                "explanation": explanation,
            }
            for text, (linked, explanation) in verdicts.items()
        ]

        statement = insert(ClassificationCacheEntry).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["namespace", "pair_hash"],
            set_={
                "linked": statement.excluded.linked,
                "explanation": statement.excluded.explanation,
            },
        )

        with self.session_factory() as session:
            session.execute(statement)
            session.commit()

    @staticmethod
    def _key(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()
