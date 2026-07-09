import ollama
import logging
from transformers import AutoTokenizer
from core.cache import PersistentEmbeddingCache
from core.schemas import CodeSemanticUnits, Element
from core.embedding.base import EmbeddingCreator

from config.constants import (
    ACTIVE_EMBEDDING_MODEL,
    ACTIVE_TOKENIZER,
    ACTIVE_MAX_TOKENS,
    DEFAULT_EMBEDDING_BATCH_SIZE
)


logger = logging.getLogger(__name__)


class OllamaEmbeddingCreator(EmbeddingCreator):

    DEFAULT_MODEL = ACTIVE_EMBEDDING_MODEL
    TOKENIZER_NAME = ACTIVE_TOKENIZER
    MAX_TOKENS = ACTIVE_MAX_TOKENS
    DEFAULT_BATCH_SIZE = DEFAULT_EMBEDDING_BATCH_SIZE

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        persistent_cache_path: str | None = None,
        cache_namespace: str | None = None
    ):
        self.model = model
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        self._tokenizer = AutoTokenizer.from_pretrained(self.TOKENIZER_NAME)
        namespace = cache_namespace or f"{self.model}:{self.TOKENIZER_NAME}:{self.MAX_TOKENS}"
        self._persistent_cache = (
            PersistentEmbeddingCache(persistent_cache_path, namespace)
            if persistent_cache_path
            else None
        )

    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        contents = []
        for e in elements:
            embedding_text = self._build_embedding_text(e)
            tokens = self._tokenizer.encode(embedding_text)
            if len(tokens) > self.MAX_TOKENS:
                logger.warning(f"Truncating: {e.identifier} ({len(tokens)} tokens)")
            contents.append(self._truncate_content(embedding_text))
        
        uncached_texts = []
        for text in set(contents):
            if text in self._cache:
                continue

            cached_embedding = self._persistent_cache.get(text) if self._persistent_cache else None
            if cached_embedding is not None:
                self._cache[text] = cached_embedding
            else:
                uncached_texts.append(text)

        for batch_start in range(0, len(uncached_texts), self.batch_size):
            texts = uncached_texts[batch_start:batch_start + self.batch_size]
            response = ollama.embed(model=self.model, input=texts)
            for text, embedding in zip(texts, response["embeddings"]):
                self._cache[text] = embedding
                if self._persistent_cache:
                    self._persistent_cache.set(text, embedding)
        return [self._cache[text] for text in contents]

    def _build_embedding_text(self, element: Element) -> str:
        if element.semantic_units is None:
            return element.content

        semantic_text = self._format_semantic_units(element.semantic_units)
        if not semantic_text:
            return element.content

        implementation_excerpt = self._truncate_to_tokens(
            element.content,
            max_tokens=max(128, self.MAX_TOKENS // 3)
        )

        parts = [
            semantic_text,
            "Implementation excerpt:",
            implementation_excerpt
        ]
        return "\n".join(part for part in parts if part.strip())

    def _format_semantic_units(self, units: CodeSemanticUnits) -> str:
        lines = []
        if units.class_name:
            lines.append(f"Class name: {units.class_name}")
        if units.class_comment:
            lines.append(f"Class comment: {units.class_comment}")
        if units.class_attributes:
            lines.append(f"Class attributes: {', '.join(units.class_attributes)}")
        if units.method_name:
            lines.append(f"Method name: {units.method_name}")
        if units.method_comment:
            lines.append(f"Method comment: {units.method_comment}")
        if units.method_params:
            lines.append(f"Method parameters: {', '.join(units.method_params)}")
        if units.return_type:
            lines.append(f"Return type: {units.return_type}")
        return "\n".join(lines)

    def _truncate_content(self, content: str) -> str:
        tokens = self._tokenizer.encode(content)
        if len(tokens) <= self.MAX_TOKENS:
            return content

        sentences = content.split('. ')
        result = ''
        for sentence in sentences:
            candidate = result + sentence + '. '
            if len(self._tokenizer.encode(candidate)) > self.MAX_TOKENS:
                break
            result = candidate
        if result:
            return result.strip()

        return self._truncate_to_tokens(content, self.MAX_TOKENS)

    def _truncate_to_tokens(self, content: str, max_tokens: int) -> str:
        tokens = self._tokenizer.encode(content)
        if len(tokens) <= max_tokens:
            return content
        return self._tokenizer.decode(tokens[:max_tokens], skip_special_tokens=True)
