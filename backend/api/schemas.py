from pydantic import BaseModel
from enum import Enum


class SourceType(str, Enum):
    DOCUMENT = "document"
    TEXT = "text"


class PreprocessorType(str, Enum):
    SINGLE = "single"
    SENTENCE = "sentence"
    SECTION = "section"
    SUMMARIZE = "summarize"
    LINE = "line"
    METHOD = "method"
    TREE = "tree"


class ClassifierType(str, Enum):
    SIMPLE = "simple"
    REASONING = "reasoning"


class AnalysisMode(str, Enum):
    SESSION = "session"
    PROJECT = "project"


class AnalyzeRequest(BaseModel):
    source_type: SourceType = SourceType.DOCUMENT
    requirements_path: str = ""
    requirements_text: str = ""
    codebase_path: str 
    source_preprocessor: PreprocessorType = PreprocessorType.SECTION
    target_preprocessor: PreprocessorType = PreprocessorType.METHOD
    classifier: ClassifierType = ClassifierType.REASONING
    n_results: int = 10
    source_granularity: int = 0
    target_granularity: int = 0
    dependency_expansion_depth: int = 0
    analysis_mode: AnalysisMode = AnalysisMode.SESSION
    project_id: str | None = None


class TraceLinkResponse(BaseModel):
    source_id: str
    target_id: str
    confidence: float
    confidence_level: str
    explanation: str | None = None


class AnalyzeResponse(BaseModel):
    trace_links: list[TraceLinkResponse]
    unimplemented: list[dict]
    summary: dict
