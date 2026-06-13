from abc import ABC, abstractmethod
from core.schemas import Element

class EmbeddingCreator(ABC):

    @abstractmethod
    def create_embeddings(self, elements: list[Element]) -> list[list[float]]:
        pass

    def create_embedding(self, element: Element) -> list[float]:
        return self.create_embeddings([element])[0]