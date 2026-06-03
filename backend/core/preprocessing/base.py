from abc import ABC, abstractmethod
from core.schemas import Artifact, Element

class Preprocessor(ABC):
    @abstractmethod
    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        pass