import ollama
import logging
import numpy as np
from transformers import AutoTokenizer
from langchain_text_splitters import RecursiveCharacterTextSplitter
from core.schemas import Element
from core.embedding.base import EmbeddingCreator

from config.constants import (
    ACTIVE_EMBEDDING_MODEL,
    ACTIVE_TOKENIZER,
    ACTIVE_MAX_TOKENS,
    DEFAULT_EMBEDDING_BATCH_SIZE
)


logger = logging.getLogger(__name__)


class OllamaEmbeddingCreator(EmbeddingCreator):

    def __init__(
            self,
            model: str = ACTIVE_EMBEDDING_MODEL,
            tokenizer_name: str = ACTIVE_TOKENIZER,
            max_tokens: int = ACTIVE_MAX_TOKENS,
            batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE):
        self.model = model
        self.max_tokens = max_tokens
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
        self._splitter = RecursiveCharacterTextSplitter(chunk_size=max_tokens, chunk_overlap=max_tokens // 5, length_function=lambda text: len(self._tokenizer.encode(text)))

    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        embeddings = []
        for element in elements:
            embedding = self._get_embedding(element.content, element.identifier)
            embeddings.append(embedding)
        return embeddings

    def _get_embedding(self, content: str, identifier: str = "") -> list[float]:
        if content in self._cache:
            return self._cache[content]

        tokens = self._tokenizer.encode(content)
        if len(tokens) <= self.max_tokens:
            embedding = self._embed_texts([content])[0]
        else:
            logger.warning(f"Chunking for pool: {identifier} ({len(tokens)} tokens)")
            embedding = self._chunk_and_pool(content)

        self._cache[content] = embedding
        return embedding

    def _chunk_and_pool(self, content: str) -> list[float]:
        chunks = self._splitter.split_text(content)
        if not chunks:
            return self._embed_texts([content])[0]

        chunk_embeddings = []

        for batch_start in range(0, len(chunks), self.batch_size):
            batch = chunks[batch_start:batch_start + self.batch_size]
            batch_embeddings = self._embed_texts(batch)
            chunk_embeddings.extend(batch_embeddings)

        pooled = np.mean(np.asarray(chunk_embeddings, dtype=np.float32), axis=0)
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled = pooled / norm
        return pooled.tolist()

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        results = []
        for batch_start in range(0, len(texts), self.batch_size):
            batch = texts[batch_start:batch_start + self.batch_size]
            uncached = [t for t in batch if t not in self._cache]
            if uncached:
                response = ollama.embed(model=self.model, input=uncached)
                for text, emb in zip(uncached, response["embeddings"]):
                    self._cache[text] = emb
            results.extend([self._cache[t] for t in batch])
        return results
