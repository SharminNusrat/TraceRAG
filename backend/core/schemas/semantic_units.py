from pydantic import BaseModel, Field


class CodeSemanticUnits(BaseModel):
    class_name: str | None = None
    class_comment: str | None = None
    class_attributes: list[str] = Field(default_factory=list)
    method_name: str | None = None
    method_comment: str | None = None
    method_params: list[str] = Field(default_factory=list)
    return_type: str | None = None


class ModelSemanticUnits(BaseModel):
    """A component's place in the architecture, kept apart from its text.

    The text is written for the embedding model and is free to change; a
    diagram needs the relationships themselves.
    """
    name: str | None = None
    provides: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
