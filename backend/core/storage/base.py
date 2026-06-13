from abc import ABC, abstractmethod
from core.schemas import Element

class VectorStore(ABC):

    @abstractmethod
    def add_elements(self, elements: list[Element], embeddings: list[list[float]]) -> None:
        pass

    @abstractmethod
    def find_similar_elements(self, embedding: list[float], n_results: int, only_compare: bool = True) -> list[tuple[Element, float]]:
        pass

    @abstractmethod
    def get_by_id(self, identifier: str) -> Element | None:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass