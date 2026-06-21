import logging
from fastapi import APIRouter, HTTPException
from api.schemas import AnalyzeRequest, AnalyzeResponse, SourceType, TraceLinkResponse, PreprocessorType, ClassifierType
from core.ingestion import CodeProvider, DocumentProvider, TextProvider
from core.preprocessing import ArtifactPreprocessor, SentencePreprocessor, SectionPreprocessor, SummarizePreprocessor, CodeChunkingPreprocessor, CodeMethodPreprocessor, CodeTreePreprocessor
from core.embedding import OllamaEmbeddingCreator
from core.classification import SimpleClassifier, ReasoningClassifier, OllamaChatProvider, GroqChatProvider
from core.pipeline import TracePipeline
from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


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


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    try:
        pipeline = TracePipeline(
            source_provider=get_source_provider(request),
            target_provider=CodeProvider(request.codebase_path),
            source_preprocessor=get_preprocessor(request.source_preprocessor),
            target_preprocessor=get_preprocessor(request.target_preprocessor),
            embedder=OllamaEmbeddingCreator(),
            classifier=get_classifier(request.classifier),
            n_results=request.n_results,
            source_granularity=request.source_granularity,
            target_granularity=request.target_granularity,
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