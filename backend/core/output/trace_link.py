from pydantic import BaseModel
from enum import Enum

class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class TraceLink(BaseModel):
    source_id: str
    target_id: str
    confidence: float
    confidence_level: ConfidenceLevel
    explanation: str | None = None

    @staticmethod
    def confidence_to_level(confidence: float) -> ConfidenceLevel:
        if confidence >= 0.80:
            return ConfidenceLevel.HIGH
        elif confidence >= 0.65:
            return ConfidenceLevel.MEDIUM
        else:
            return ConfidenceLevel.LOW