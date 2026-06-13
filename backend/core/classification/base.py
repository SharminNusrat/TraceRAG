from abc import ABC, abstractmethod
from pydantic import BaseModel
from core.schemas import Element 

class ClassificationResult(BaseModel):
    source: Element
    target: Element
    confidence: float
    explanation: str | None = None

class Classifier(ABC):

    @abstractmethod
    def classify(self, source: Element, target_candidates: list[tuple[Element, float]]) -> list[ClassificationResult]:
        pass