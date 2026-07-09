from typing import Optional
from core.schemas.knowledge import Knowledge
from core.schemas.semantic_units import CodeSemanticUnits

class Element(Knowledge):
    """Class representing an element."""
    granularity: int
    parent_id: Optional[str] = None
    compare: bool = True
    semantic_units: Optional[CodeSemanticUnits] = None