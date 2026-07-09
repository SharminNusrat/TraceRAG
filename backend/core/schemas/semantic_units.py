from pydantic import BaseModel, Field


class CodeSemanticUnits(BaseModel):
    class_name: str | None = None
    class_comment: str | None = None
    class_attributes: list[str] = Field(default_factory=list)
    method_name: str | None = None
    method_comment: str | None = None
    method_params: list[str] = Field(default_factory=list)
    return_type: str | None = None
