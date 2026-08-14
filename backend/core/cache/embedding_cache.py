"""Persistent embedding cache, backed by the application database.

Batch-only: a project means thousands of lookups, and one at a time would be
thousands of round trips where two will do.
"""

import logging
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.db.models import EmbeddingCacheEntry
from core.db.session import SessionLocal

logger = logging.getLogger(__name__)


class PersistentEmbeddingCache:

    def __init__(self, namespace: str, session_factory=SessionLocal):
        self.namespace = namespace
        self.session_factory = session_factory

    def get_many(self, texts: list[str]) -> dict[str, list[float]]:
        """The embeddings already stored for these texts, keyed by text."""
        if not texts:
            return {}

        by_hash = {self._key(text): text for text in texts}
        with self.session_factory() as session:
            rows = session.execute(
                select(EmbeddingCacheEntry.text_hash, EmbeddingCacheEntry.embedding).where(
                    EmbeddingCacheEntry.namespace == self.namespace,
                    EmbeddingCacheEntry.text_hash.in_(list(by_hash)),
                )
            ).all()

        return {by_hash[text_hash]: embedding for text_hash, embedding in rows}

    def set_many(self, embeddings: dict[str, list[float]]) -> None:
        """Store embeddings, overwriting any entry already under that key."""
        # Upsert, not insert: two analyses running at once can produce the
        # same text, and the second must not fail the whole batch.
        if not embeddings:
            return

        rows = [
            {
                "namespace": self.namespace,
                "text_hash": self._key(text),
                "embedding": embedding,
            }
            for text, embedding in embeddings.items()
        ]

        statement = insert(EmbeddingCacheEntry).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["namespace", "text_hash"],
            set_={"embedding": statement.excluded.embedding},
        )

        with self.session_factory() as session:
            session.execute(statement)
            session.commit()

    @staticmethod
    def _key(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()
