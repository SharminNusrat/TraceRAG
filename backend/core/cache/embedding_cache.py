import json
import sqlite3
import time
from hashlib import sha256
from pathlib import Path


class PersistentEmbeddingCache:
    def __init__(self, db_path: str, namespace: str):
        self.db_path = Path(db_path)
        self.namespace = namespace
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get(self, text: str) -> list[float] | None:
        key = self._key(text)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE namespace = ? AND text_hash = ?",
                (self.namespace, key)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, text: str, embedding: list[float]) -> None:
        key = self._key(text)
        payload = json.dumps(embedding)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO embeddings(namespace, text_hash, embedding, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (self.namespace, key, payload, time.time())
            )
            conn.commit()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    namespace TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(namespace, text_hash)
                )
                """
            )
            conn.commit()

    def _key(self, text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()
