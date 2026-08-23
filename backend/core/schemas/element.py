from enum import Enum
from typing import Optional
from core.schemas.knowledge import Knowledge
from core.schemas.semantic_units import CodeSemanticUnits, ModelSemanticUnits


class ElementLevel(str, Enum):
    """What an element *is*, independent of how deeply it happens to be nested.

    `granularity` is a structural depth counter, so the same number means
    different things across preprocessors (and even within one - the method
    preprocessor emits both classes and top-level functions at depth 1). This
    enum is what users pick when they choose the level to report links at.
    """

    ARTIFACT = "artifact"    # a whole document or file, unsplit
    SECTION = "section"      # a numbered requirements section, at any heading depth
    SENTENCE = "sentence"
    PACKAGE = "package"      # a source folder
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"    # a method or a top-level function
    CHUNK = "chunk"          # a fixed-size code chunk
    COMPONENT = "component"  # an architecture model component
    INTERFACE = "interface"  # an interface a component provides or requires


class Element(Knowledge):
    """Class representing an element."""
    granularity: int
    level: ElementLevel
    parent_id: Optional[str] = None
    compare: bool = True
    semantic_units: Optional[CodeSemanticUnits] = None
    model_units: Optional[ModelSemanticUnits] = None
    # One sentence saying what this element does, for artifacts that are not
    # written in prose. Absent unless summarisation ran, and absent then too if
    # the model could not be reached - it enriches the embedding, nothing
    # depends on it.
    summary: Optional[str] = None
