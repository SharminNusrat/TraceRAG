"""Persistent summary cache, backed by the application database."""

import logging
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.db.models import SummaryCacheEntry
from core.db.session import SessionLocal

logger = logging.getLogger(__name__)


class PersistentSummaryCache:

    def __init__(self, namespace: str, session_factory=SessionLocal):
        self.namespace = namespace
        self.session_factory = session_factory

    def get_many(self, texts: list[str]) -> dict[str, str]:
        """The summaries already stored for these texts, keyed by text."""
        if not texts:
            return {}

        by_hash = {self._key(text): text for text in texts}
        with self.session_factory() as session:
            rows = session.execute(
                select(SummaryCacheEntry.text_hash, SummaryCacheEntry.summary).where(
                    SummaryCacheEntry.namespace == self.namespace,
                    SummaryCacheEntry.text_hash.in_(list(by_hash)),
                )
            ).all()

        return {by_hash[text_hash]: summary for text_hash, summary in rows}

    def set_many(self, summaries: dict[str, str]) -> None:
        """Store summaries, overwriting any entry already under that key."""
        # Upsert, not insert: two analyses running at once can produce the same
        # text, and the second must not fail the whole batch.
        if not summaries:
            return

        rows = [
            {
                "namespace": self.namespace,
                "text_hash": self._key(text),
                "summary": summary,
            }
            for text, summary in summaries.items()
        ]

        statement = insert(SummaryCacheEntry).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["namespace", "text_hash"],
            set_={"summary": statement.excluded.summary},
        )

        with self.session_factory() as session:
            session.execute(statement)
            session.commit()

    @staticmethod
    def _key(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()
