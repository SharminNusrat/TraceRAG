from abc import ABC, abstractmethod
from core.schemas import Artifact

class ArtifactProvider(ABC):
    """Abstract base class for artifact providers."""
    
    @abstractmethod
    def load(self) -> list[Artifact]:
        """Fetch artifacts from the source."""
        pass