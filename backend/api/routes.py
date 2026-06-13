import logging
from fastapi import APIRouter, HTTPException
from api.schemas import AnalyzeRequest, AnalyzeResponse, TraceLinkResponse, PreprocessorType, ClassifierType
from core.ingestion.pdf_provider import PDFProvider
from core.ingestion.code_provider import CodeProvider
from core.preprocessing.artifact_preprocessor import ArtifactPreprocessor
from core.preprocessing.sentence_preprocessor import SentencePreprocessor
from core.preprocessing.section_preprocessor import SectionPreprocessor
from core.preprocessing.summarize_preprocessor import SummarizePreprocessor
from core.preprocessing.code_chunking_preprocessor import CodeChunkingPreprocessor
from core.preprocessing.code_method_preprocessor import CodeMethodPreprocessor
from core.preprocessing.code_tree_preprocessor import CodeTreePreprocessor
from core.embedding.ollama_embedder import OllamaEmbeddingCreator
from core.classification.simple_classifier import SimpleClassifier
from core.classification.reasoning_classifier import ReasoningClassifier
from core.classification.ollama_chat_provider import OllamaChatProvider
from core.pipeline import TracePipeline

router = APIRouter()
logger = logging.getLogger(__name__)


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
    provider = OllamaChatProvider()
    match classifier_type:
        case ClassifierType.SIMPLE:
            return SimpleClassifier(provider=provider)
        case ClassifierType.REASONING:
            return ReasoningClassifier(provider=provider)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    try:
        pipeline = TracePipeline(
            source_provider=PDFProvider(request.requirements_path),
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