import json
import logging
import os
import re
import shutil
import zipfile
from hashlib import sha256
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from api.schemas import (
    AnalysisMode, AnalyzeRequest, AnalyzeResponse, CapabilitiesResponse, ElementResponse,
    SourceType, TraceLinkResponse, PreprocessorType, ClassifierType,
)
from api.capabilities import (
    ARTIFACT_KINDS_BY_KEY, MAX_TOTAL_UPLOAD_BYTES, MAX_UPLOAD_BYTES,
    ROLE_SOURCE, ROLE_TARGET, get_capabilities,
)
from core.schemas import ElementLevel
from core.ingestion import CodeProvider, DocumentProvider, TextProvider
from core.preprocessing import ArtifactPreprocessor, SentencePreprocessor, SectionPreprocessor, SummarizePreprocessor, CodeChunkingPreprocessor, CodeMethodPreprocessor, CodeTreePreprocessor
from core.embedding import OllamaEmbeddingCreator
from core.classification import SimpleClassifier, ReasoningClassifier, OllamaChatProvider, GroqChatProvider
from core.dependency import CodeDependencyAnalyzer
from core.pipeline import TracePipeline
from core.projects import artifact_store
from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_DATA_ROOT = Path("./chroma_data/projects")


def request_default(field: str):
    """Default for a form field, taken from AnalyzeRequest.

    Keeps schemas.py the single place to change a default: editing
    `analysis_mode` or `dependency_expansion_depth` there now affects the
    upload endpoint too, instead of only the JSON one.
    """
    return AnalyzeRequest.model_fields[field].default


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


def get_chroma_path(project_id: str) -> str:
    """Where this project's vectors are indexed."""
    return str(PROJECT_DATA_ROOT / sanitize_project_id(project_id) / "chroma")




def build_pipeline_response(
    source_provider,
    target_provider,
    source_preprocessor: PreprocessorType,
    target_preprocessor: PreprocessorType,
    classifier: ClassifierType,
    n_results: int,
    source_output_level: ElementLevel | None,
    target_output_level: ElementLevel | None,
    dependency_expansion_depth: int,
    chroma_path: str,
    use_persistent_cache: bool,
    reset_vector_stores: bool,
) -> AnalyzeResponse:
    pipeline = TracePipeline(
        source_provider=source_provider,
        target_provider=target_provider,
        source_preprocessor=get_preprocessor(source_preprocessor),
        target_preprocessor=get_preprocessor(target_preprocessor),
        embedder=OllamaEmbeddingCreator(use_persistent_cache=use_persistent_cache),
        classifier=get_classifier(classifier),
        chroma_path=chroma_path,
        n_results=n_results,
        source_output_level=source_output_level,
        target_output_level=target_output_level,
        dependency_analyzer=CodeDependencyAnalyzer() if dependency_expansion_depth > 0 else None,
        dependency_expansion_depth=dependency_expansion_depth,
        reset_vector_stores=reset_vector_stores,
    )
    result = pipeline.run().to_dict()

    return AnalyzeResponse(
        trace_links=[TraceLinkResponse(**link) for link in result["trace_links"]],
        source_elements=[ElementResponse(**e) for e in result["source_elements"]],
        target_elements=[ElementResponse(**e) for e in result["target_elements"]],
        unimplemented=result["unimplemented"],
        summary=result["summary"],
    )


def run_analysis(request: AnalyzeRequest) -> AnalyzeResponse:
    chroma_path = "./chroma_data/session"
    use_persistent_cache = False
    reset_vector_stores = True

    if request.analysis_mode == AnalysisMode.PROJECT:
        project_id = get_project_id(request)
        chroma_path = get_chroma_path(project_id)
        use_persistent_cache = True
        reset_vector_stores = False
        logger.info(f"Using project mode with project_id={project_id}")
    else:
        logger.info("Using session mode")

    return build_pipeline_response(
        source_provider=get_source_provider(request),
        target_provider=CodeProvider(request.codebase_path),
        source_preprocessor=request.source_preprocessor,
        target_preprocessor=request.target_preprocessor,
        classifier=request.classifier,
        n_results=request.n_results,
        source_output_level=request.source_output_level,
        target_output_level=request.target_output_level,
        dependency_expansion_depth=request.dependency_expansion_depth,
        chroma_path=chroma_path,
        use_persistent_cache=use_persistent_cache,
        reset_vector_stores=reset_vector_stores,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities():
    """What the pipeline can ingest. Drives the frontend's option lists."""
    return get_capabilities()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    try:
        return run_analysis(request)
    except Exception as e:
        import traceback
        logger.error(f"Pipeline failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


# ----- Upload handling -----

SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.\- ]+")


class UploadBudget:
    """Tracks bytes written across a whole request, not just per file."""

    def __init__(self, total_limit: int = MAX_TOTAL_UPLOAD_BYTES):
        self.total_limit = total_limit
        self.used = 0

    def consume(self, size: int, label: str) -> None:
        self.used += size
        if self.used > self.total_limit:
            limit_mb = self.total_limit // (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds the {limit_mb} MB total limit (adding '{label}').",
            )


def safe_relative_path(raw_path: str) -> Path:
    """Turn a browser-supplied relative path into a contained, sanitised path."""
    parts = []
    for segment in re.split(r"[\/]+", raw_path or ""):
        segment = segment.strip()
        if not segment or segment in {".", ".."}:
            continue
        parts.append(SAFE_SEGMENT.sub("-", segment))
    return Path(*parts) if parts else Path("file")


def save_upload(upload: UploadFile, destination: Path, budget: UploadBudget) -> int:
    """Stream an upload to disk, enforcing per-file and total size limits."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(destination, "wb") as target:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                raise HTTPException(
                    status_code=413,
                    detail=f"'{upload.filename}' exceeds the {limit_mb} MB per-file limit.",
                )
            budget.consume(len(chunk), upload.filename or "file")
            target.write(chunk)
    return written


def extract_archive(archive_path: Path, destination: Path, budget: UploadBudget) -> None:
    """Extract a zip, skipping entries that would escape the destination."""
    if not zipfile.is_zipfile(archive_path):
        raise HTTPException(
            status_code=400,
            detail=f"'{archive_path.name}' is not a valid .zip archive.",
        )

    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target_path = (destination / safe_relative_path(member.filename)).resolve()
            if not target_path.is_relative_to(resolved_destination):
                logger.warning(f"Skipping unsafe archive entry: {member.filename}")
                continue
            budget.consume(member.file_size, member.filename)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target_path, "wb") as target:
                shutil.copyfileobj(source, target)
    archive_path.unlink(missing_ok=True)


def resolve_side(artifacts: list[dict], artifact_ids: list[str], role: str) -> list[dict]:
    """Validate the artifacts chosen for one side of the trace.

    A side may hold several artifacts - they are analysed together as one
    corpus - but they must all be of the same kind so that a single
    preprocessor and provider apply to the whole side.
    """
    if not artifact_ids:
        raise HTTPException(status_code=400, detail=f"Select at least one {role} artifact.")

    chosen = []
    for artifact_id in artifact_ids:
        artifact = next((a for a in artifacts if a.get("id") == artifact_id), None)
        if artifact is None:
            raise HTTPException(
                status_code=400,
                detail=f"No uploaded artifact matches '{artifact_id}'.",
            )

        kind = ARTIFACT_KINDS_BY_KEY.get(artifact.get("kind"))
        if kind is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown artifact kind '{artifact.get('kind')}'.",
            )
        if role not in kind.roles:
            raise HTTPException(
                status_code=400,
                detail=f"'{kind.label}' artifacts cannot be used as the {role} of a trace.",
            )
        chosen.append(artifact)

    kinds = {artifact.get("kind") for artifact in chosen}
    if len(kinds) > 1:
        labels = sorted(ARTIFACT_KINDS_BY_KEY[k].label for k in kinds)
        raise HTTPException(
            status_code=400,
            detail=f"The {role} side mixes {' and '.join(labels)} artifacts. Group one kind at a time.",
        )

    return chosen


def materialise_side(
    artifacts: list[dict],
    uploads: list[UploadFile],
    paths: list[str],
    side_dir: Path,
    budget: UploadBudget,
) -> Path:
    """Write every artifact on one side into a single directory.

    Files normally land at their own relative path, which keeps identifiers
    readable (`services/auth.js`). Folder uploads already carry their folder
    name so they rarely clash; only when two artifacts would actually overwrite
    each other is the colliding file nested under its artifact name.
    """
    side_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()

    for artifact in artifacts:
        kind = ARTIFACT_KINDS_BY_KEY[artifact["kind"]]
        artifact_name = artifact.get("name") or artifact.get("id") or kind.key
        namespace = Path(sanitize_project_id(artifact_name))

        text = (artifact.get("text") or "").strip()
        if text:
            if not kind.accepts_text:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{kind.label}' artifacts cannot be provided as pasted text.",
                )
            # Persist pasted text as a real document so one side can mix typed
            # text with uploaded files behind a single provider.
            destination = side_dir / f"{sanitize_project_id(artifact_name)}.txt"
            destination.write_text(text, encoding="utf-8")
            written.add(destination.name)
            continue

        indexes = artifact.get("file_indexes") or []
        if not indexes:
            raise HTTPException(
                status_code=400,
                detail=f"Artifact '{artifact_name}' has no files.",
            )

        allowed = set(kind.extensions)
        for index in indexes:
            if not isinstance(index, int) or not 0 <= index < len(uploads):
                raise HTTPException(status_code=400, detail=f"File index {index} is out of range.")

            upload = uploads[index]
            raw_path = paths[index] if index < len(paths) else (upload.filename or "")
            relative = safe_relative_path(raw_path or upload.filename or "file")
            suffix = relative.suffix.lower()

            if suffix == ".zip":
                if not kind.accepts_archive:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{kind.label}' artifacts do not accept .zip archives.",
                    )
                staged = side_dir / f"__archive_{index}.zip"
                save_upload(upload, staged, budget)
                # Several archives on one side each get their own subtree.
                destination = side_dir if len(artifacts) == 1 else side_dir / namespace
                extract_archive(staged, destination, budget)
                continue

            if suffix not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{relative.name}' is not a supported {kind.label.lower()} file. "
                        f"Accepted: {', '.join(sorted(allowed))}"
                        + (" or a .zip archive." if kind.accepts_archive else ".")
                    ),
                )

            if str(relative) in written:
                relative = namespace / relative
            written.add(str(relative))
            save_upload(upload, side_dir / relative, budget)

    return side_dir


def side_manifest(artifacts: list[dict], role: str) -> dict:
    """Describe one side of the trace for later storage."""
    # The stored unit is the side, not the artifact: materialise_side writes a
    # whole side into one directory, which is what makes it a single corpus.
    names = [artifact.get("name") or artifact.get("id") or "artifact" for artifact in artifacts]
    return {
        "role": role,
        "artifact_type": artifacts[0]["kind"],
        "name": ", ".join(names)[:255],
        "directory": role,
    }


def build_provider(kind_key: str, side_dir: Path):
    """Pick the ingestion provider for an artifact kind.

    Extend this alongside the ARTIFACT_KINDS registry when adding a new type.
    Every side reads from a directory, so pasted text is written out as a file
    rather than going through TextProvider.
    """
    match kind_key:
        case "requirements":
            return DocumentProvider(str(side_dir))
        case "code":
            return CodeProvider(str(side_dir))

    raise HTTPException(status_code=400, detail=f"No provider registered for '{kind_key}'.")


def relativize(identifier: str, roots: list[Path]) -> str:
    """Strip the temp workspace prefix so identifiers read as project paths."""
    for root in roots:
        prefix = f"{root}{os.sep}"
        if identifier.startswith(prefix):
            return identifier[len(prefix):].replace(os.sep, "/")
    return identifier


def relativize_text(text: str, roots: list[Path]) -> str:
    """Strip workspace prefixes anywhere inside free text.

    Dependency-expansion explanations quote element identifiers, which would
    otherwise surface the temp upload path to the user.
    """
    for root in roots:
        for prefix in (f"{root}{os.sep}", f"{root}/"):
            text = text.replace(prefix, "")
    return text


def relativize_response(response: AnalyzeResponse, roots: list[Path]) -> AnalyzeResponse:
    for link in response.trace_links:
        link.source_id = relativize(link.source_id, roots)
        link.target_id = relativize(link.target_id, roots)
        if link.explanation:
            link.explanation = relativize_text(link.explanation, roots)
    for element in (*response.source_elements, *response.target_elements):
        element.identifier = relativize(element.identifier, roots)
        if element.parent_id:
            element.parent_id = relativize(element.parent_id, roots)
    for item in response.unimplemented:
        if "identifier" in item:
            item["identifier"] = relativize(item["identifier"], roots)
    return response


def parse_json_field(raw: str, field: str, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail=f"'{field}' is not valid JSON: {error.msg}")


def upload_project_id(artifacts: list[dict], paths: list[str], explicit: str | None) -> str:
    """A project id that is stable across runs of the same upload.

    Uploads land in a fresh temp directory every time, so hashing the codebase
    path (as the path-based endpoint does) would mint a new project - and a new
    empty embedding cache - on every run. Hash the artifact manifest instead.
    """
    if explicit:
        return sanitize_project_id(explicit)

    parts = []
    for artifact in artifacts:
        members = sorted(
            safe_relative_path(paths[index]).as_posix()
            for index in (artifact.get("file_indexes") or [])
            if isinstance(index, int) and index < len(paths)
        )
        parts.append(f"{artifact.get('kind')}|{artifact.get('name')}|{','.join(members)}")

    digest = sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:10]
    label = sanitize_project_id(artifacts[0].get("name") or "project") if artifacts else "project"
    return sanitize_project_id(f"{label}-{digest}")


def parse_id_list(raw: str, field: str) -> list[str]:
    value = parse_json_field(raw, field, None)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise HTTPException(status_code=400, detail=f"'{field}' must be a JSON array of artifact ids.")


@router.post("/analyze/upload", response_model=AnalyzeResponse)
async def analyze_upload(
    artifacts: str = Form(...),
    source_artifact_ids: str = Form(...),
    target_artifact_ids: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    file_paths: str = Form("[]"),
    source_preprocessor: PreprocessorType = Form(request_default("source_preprocessor")),
    target_preprocessor: PreprocessorType = Form(request_default("target_preprocessor")),
    source_output_level: ElementLevel | None = Form(request_default("source_output_level")),
    target_output_level: ElementLevel | None = Form(request_default("target_output_level")),
    classifier: ClassifierType = Form(request_default("classifier")),
    n_results: int = Form(request_default("n_results")),
    dependency_expansion_depth: int = Form(request_default("dependency_expansion_depth")),
    analysis_mode: AnalysisMode = Form(request_default("analysis_mode")),
    project_id: str | None = Form(None),
):
    """Run the pipeline over browser-uploaded artifacts.

    `artifacts` is a JSON array describing each uploaded artifact:
        [{"id","name","kind","file_indexes":[...]}, {"id","name","kind","text"}]
    where `file_indexes` point into `files`, and `file_paths` carries each
    file's relative path so folder uploads keep their structure.

    Each side takes a list of artifact ids. Several artifacts on one side are
    analysed together as a single corpus - that is how a set of loose code
    files becomes one codebase. Only the referenced artifacts are written to
    disk.

    Defaults (including analysis_mode) come from AnalyzeRequest, so both
    endpoints stay in step until auth lands and the mode becomes per-user.
    """
    artifact_list = parse_json_field(artifacts, "artifacts", [])
    if not isinstance(artifact_list, list) or not artifact_list:
        raise HTTPException(status_code=400, detail="Upload at least one artifact.")

    paths = parse_json_field(file_paths, "file_paths", [])
    if not isinstance(paths, list):
        raise HTTPException(status_code=400, detail="'file_paths' must be a JSON array.")

    source_ids = parse_id_list(source_artifact_ids, "source_artifact_ids")
    target_ids = parse_id_list(target_artifact_ids, "target_artifact_ids")

    if set(source_ids) & set(target_ids):
        raise HTTPException(
            status_code=400,
            detail="An artifact cannot be on both sides of a trace.",
        )

    source_artifacts = resolve_side(artifact_list, source_ids, ROLE_SOURCE)
    target_artifacts = resolve_side(artifact_list, target_ids, ROLE_TARGET)

    chroma_path = "./chroma_data/session"
    use_persistent_cache = False
    reset_vector_stores = True

    if analysis_mode == AnalysisMode.PROJECT:
        # Project mode persists the embedding cache between runs, so re-running
        # the same codebase skips re-embedding unchanged content.
        resolved_project_id = upload_project_id(
            source_artifacts + target_artifacts, paths, project_id
        )
        chroma_path = get_chroma_path(resolved_project_id)
        use_persistent_cache = True
        reset_vector_stores = False
        logger.info(f"Using project mode with project_id={resolved_project_id}")
    else:
        logger.info("Using session mode")

    # The workspace outlives the request now: saving the run is a separate call
    # that may not come for minutes, and the files have to still be there when
    # it does. Anything nobody saves is reaped on the retention window.
    artifact_store.purge_expired_uploads()
    upload_id, workspace = artifact_store.create_upload_dir()

    budget = UploadBudget()
    try:
        source_dir = materialise_side(source_artifacts, files, paths, workspace / "source", budget)
        target_dir = materialise_side(target_artifacts, files, paths, workspace / "target", budget)
        artifact_store.write_manifest(workspace, [
            side_manifest(source_artifacts, ROLE_SOURCE),
            side_manifest(target_artifacts, ROLE_TARGET),
        ])

        response = build_pipeline_response(
            source_provider=build_provider(source_artifacts[0]["kind"], source_dir),
            target_provider=build_provider(target_artifacts[0]["kind"], target_dir),
            source_preprocessor=source_preprocessor,
            target_preprocessor=target_preprocessor,
            classifier=classifier,
            n_results=n_results,
            source_output_level=source_output_level,
            target_output_level=target_output_level,
            dependency_expansion_depth=dependency_expansion_depth,
            chroma_path=chroma_path,
            use_persistent_cache=use_persistent_cache,
            reset_vector_stores=reset_vector_stores,
        )
        response.upload_id = upload_id
        return relativize_response(response, [source_dir, target_dir])
    except HTTPException:
        # A rejected upload produced no result, so there is nothing to save and
        # nothing worth keeping on disk.
        artifact_store.discard_upload(upload_id)
        raise
    except Exception as e:
        import traceback
        artifact_store.discard_upload(upload_id)
        logger.error(f"Pipeline failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
