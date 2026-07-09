from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DependencyType(str, Enum):
    IMPORT = "import"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    CALLS = "calls"
    INSTANTIATES = "instantiates"
    FIELD_TYPE = "field_type"
    PARAM_TYPE = "param_type"
    RETURN_TYPE = "return_type"
    PACKAGE_MEMBER = "package_member"
    SAME_FILE = "same_file"
    SAME_CLASS = "same_class"


class CodeDependency(BaseModel):
    source_id: str
    dependency_type: DependencyType
    target_id: str | None = None
    target_name: str | None = None
    confidence: float = 1.0
    evidence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)