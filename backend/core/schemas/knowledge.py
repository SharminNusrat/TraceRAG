from abc import ABC
from pydantic import BaseModel

class Knowledge(ABC, BaseModel):
    """Base class for knowledge objects."""
    identifier: str
    type: str
    content: str