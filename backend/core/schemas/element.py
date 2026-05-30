from typing import Optional
from core.schemas.knowledge import Knowledge

class Element(Knowledge):
    """Class representing an element."""
    granularity: str
    parent_id: Optional[str] = None
    compare: bool = True