import ollama
import logging
from transformers import AutoTokenizer
from core.schemas import Element
from core.embedding.base import EmbeddingCreator


logger = logging.getLogger(__name__)


class OllamaEmbeddingCreator(EmbeddingCreator):

    # Embedding model: BGE-large-en
    # DEFAULT_MODEL = "qllama/bge-large-en-v1.5"
    # TOKENIZER_NAME = "BAAI/bge-large-en-v1.5"
    # MAX_TOKENS = 512

    # Embedding model: Nomic-embed-text
    DEFAULT_MODEL = "nomic-embed-text"
    TOKENIZER_NAME = "nomic-ai/nomic-embed-text-v1"
    MAX_TOKENS = 8192

    # Embedding model: BGE-m3
    # DEFAULT_MODEL = "bge-m3"
    # TOKENIZER_NAME = "BAAI/bge-m3"
    # MAX_TOKENS = 8192
    DEFAULT_BATCH_SIZE = 16

    def __init__(self, model: str = DEFAULT_MODEL, batch_size: int = DEFAULT_BATCH_SIZE):
        self.model = model
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        self._tokenizer = AutoTokenizer.from_pretrained(self.TOKENIZER_NAME)

    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        contents = []
        for e in elements:
            tokens = self._tokenizer.encode(e.content)
            if len(tokens) > self.MAX_TOKENS:
                logger.warning(f"Truncating: {e.identifier} ({len(tokens)} tokens)")
            contents.append(self._truncate_content(e.content))
        
        uncached_texts = [text for text in set(contents) if text not in self._cache]

        for batch_start in range(0, len(uncached_texts), self.batch_size):
            texts = uncached_texts[batch_start:batch_start + self.batch_size]
            response = ollama.embed(model=self.model, input=texts)
            for text, embedding in zip(texts, response["embeddings"]):
                self._cache[text] = embedding
        return [self._cache[text] for text in contents]

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
        return result.strip()