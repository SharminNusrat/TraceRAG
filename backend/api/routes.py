import logging
import os
import re
from hashlib import sha256
from pathlib import Path
from fastapi import APIRouter, HTTPException
from api.schemas import AnalysisMode, AnalyzeRequest, AnalyzeResponse, SourceType, TraceLinkResponse, PreprocessorType, ClassifierType
from core.ingestion import CodeProvider, DocumentProvider, TextProvider
from core.preprocessing import ArtifactPreprocessor, SentencePreprocessor, SectionPreprocessor, SummarizePreprocessor, CodeChunkingPreprocessor, CodeMethodPreprocessor, CodeTreePreprocessor
from core.embedding import OllamaEmbeddingCreator
from core.classification import SimpleClassifier, ReasoningClassifier, OllamaChatProvider, GroqChatProvider
from core.dependency import CodeDependencyAnalyzer
from core.pipeline import TracePipeline
from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_DATA_ROOT = Path("./chroma_data/projects")


def get_source_provider(request: AnalyzeRequest):
    match request.source_type:
        case SourceType.DOCUMENT:
            return DocumentProvider(request.requirements_path)
        case SourceType.TEXT:
            return TextProvider(request.requirements_text)


def get_preprocessor(preprocessor_type: PreprocessorType):
    match preprocessor_type:
        case PreprocessorType.SINGLE:
            return ArtifactPreprocessor()
        case PreprocessorType.SENTENCE:
            return SentencePreprocessor()
        case PreprocessorType.SECTION:
            return SectionPreprocessor()
        case PreprocessorType.SUMMARIZE:
            return SummarizePreprocessor()
        case PreprocessorType.LINE:
            return CodeChunkingPreprocessor()
        case PreprocessorType.METHOD:
            return CodeMethodPreprocessor()
        case PreprocessorType.TREE:
            return CodeTreePreprocessor()


def get_classifier(classifier_type: ClassifierType):
    # provider = OllamaChatProvider() # Working perfectly
    provider = GroqChatProvider(api_keys=settings.groq_api_keys_list) # Also working
    match classifier_type:
        case ClassifierType.SIMPLE:
            return SimpleClassifier(provider=provider)
        case ClassifierType.REASONING:
            return ReasoningClassifier(provider=provider)


def get_project_id(request: AnalyzeRequest) -> str:
    if request.project_id:
        return sanitize_project_id(request.project_id)

    codebase_path = os.path.abspath(request.codebase_path)
    basename = os.path.basename(codebase_path.rstrip("\\/")) or "project"
    digest = sha256(codebase_path.encode("utf-8")).hexdigest()[:10]
    return sanitize_project_id(f"{basename}-{digest}")


def sanitize_project_id(project_id: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", project_id.strip())
    return sanitized.strip("-") or "project"


def get_project_paths(request: AnalyzeRequest) -> tuple[str, str]:
    project_id = get_project_id(request)
    project_root = PROJECT_DATA_ROOT / project_id
    return str(project_root / "chroma"), str(project_root / "embeddings.sqlite")


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    try:
        chroma_path = "./chroma_data/session"
        persistent_cache_path = None
        reset_vector_stores = True

        if request.analysis_mode == AnalysisMode.PROJECT:
            chroma_path, persistent_cache_path = get_project_paths(request)
            reset_vector_stores = False
            logger.info(f"Using project mode with project_id={get_project_id(request)}")
        else:
            logger.info("Using session mode")

        pipeline = TracePipeline(
            source_provider=get_source_provider(request),
            target_provider=CodeProvider(request.codebase_path),
            source_preprocessor=get_preprocessor(request.source_preprocessor),
            target_preprocessor=get_preprocessor(request.target_preprocessor),
            embedder=OllamaEmbeddingCreator(persistent_cache_path=persistent_cache_path),
            classifier=get_classifier(request.classifier),
            chroma_path=chroma_path,
            n_results=request.n_results,
            source_granularity=request.source_granularity,
            target_granularity=request.target_granularity,
            dependency_analyzer=CodeDependencyAnalyzer() if request.dependency_expansion_depth > 0 else None,
            dependency_expansion_depth=request.dependency_expansion_depth,
            reset_vector_stores=reset_vector_stores,
        )
        matrix = pipeline.run()
        result = matrix.to_dict()

        return AnalyzeResponse(
            trace_links=[TraceLinkResponse(**link) for link in result["trace_links"]],
            unimplemented=result["unimplemented"],
            summary=result["summary"]
        )
    except Exception as e:
        import traceback
        logger.error(f"Pipeline failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
