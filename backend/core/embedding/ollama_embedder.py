import ollama
from transformers import AutoTokenizer
from core.schemas import Element
from core.embedding.base import EmbeddingCreator

class OllamaEmbeddingCreator(EmbeddingCreator):

    DEFAULT_MODEL = "qllama/bge-large-en-v1.5"
    TOKENIZER_NAME = "BAAI/bge-large-en-v1.5"
    MAX_TOKENS = 512
    DEFAULT_BATCH_SIZE = 16

    def __init__(self, model: str = DEFAULT_MODEL, batch_size: int = DEFAULT_BATCH_SIZE):
        self.model = model
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        self._tokenizer = AutoTokenizer.from_pretrained(self.TOKENIZER_NAME)

    # def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
    #     contents = [self._truncate_content(element.content) for element in elements]
    #     uncached = [(i, c) for i, c in enumerate(contents) if c not in self._cache]

    #     for batch_start in range(0, len(uncached), self.batch_size):
    #         batch = uncached[batch_start:batch_start + self.batch_size]
    #         indices, texts = zip(*batch)
    #         response = ollama.embed(model=self.model, input=list(texts))
    #         for i, embedding in zip(indices, response["embeddings"]):
    #             self._cache[contents[i]] = embedding
                
    #     return [self._cache[content] for content in contents]

    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        contents = [self._truncate_content(e.content) for e in elements]
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