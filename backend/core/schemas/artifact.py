from enum import Enum
from core.schemas.knowledge import Knowledge

class ArtifactType(str, Enum):
    """Enumeration of artifact types."""
    REQUIREMENT = "requirement"
    SOURCE_CODE = "source code"

class Artifact(Knowledge):
    """Class representing an artifact."""
    pass