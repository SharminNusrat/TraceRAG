import ollama
from transformers import AutoTokenizer
from core.schemas import Element
from core.embedding.base import EmbeddingCreator

class OllamaEmbeddingCreator(EmbeddingCreator):

    DEFAULT_MODEL = "qllama/bge-large-en-v1.5"
    TOKENIZER_NAME = "BAAI/bge-large-en-v1.5"
    MAX_TOKENS = 512

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._cache: dict[str, list[float]] = {}
        self._tokenizer = AutoTokenizer.from_pretrained(self.TOKENIZER_NAME)

    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        embeddings = []
        for element in elements:
            embedding = self._get_embedding(element)
            embeddings.append(embedding)
        return embeddings

    def _get_embedding(self, element: Element) -> list[float]:
        content = self._truncate_content(element.content)
        if content in self._cache:
            return self._cache[content]
        response = ollama.embeddings(model=self.model, prompt=content)
        embedding = response['embedding']
        self._cache[content] = embedding
        return embedding

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