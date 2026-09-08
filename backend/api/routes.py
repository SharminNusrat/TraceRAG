import json
import logging
import os
import re
import shutil
import time
import zipfile
from hashlib import sha256
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from api.schemas import (
    AnalysisMode, AnalysisStartResponse, AnalyzeRequest, AnalyzeResponse, ArtifactInput,
    CapabilitiesResponse, ElementResponse, TraceLinkResponse, PreprocessorType, ClassifierType,
)
from api.capabilities import (
    ARTIFACT_KINDS_BY_KEY, KIND_CODE, MAX_TOTAL_UPLOAD_BYTES, MAX_UPLOAD_BYTES,
    ROLE_SOURCE, ROLE_TARGET, get_capabilities,
)
from core.schemas import ElementLevel
from core.ingestion import CodeProvider, DocumentProvider, ModelProvider, TextProvider
from core.preprocessing import ArtifactPreprocessor, SentencePreprocessor, SectionPreprocessor, SummarizePreprocessor, CodeChunkingPreprocessor, CodeMethodPreprocessor, CodeTreePreprocessor, ModelUmlPreprocessor
from core.embedding import OllamaEmbeddingCreator
from core.classification import SimpleClassifier, ReasoningClassifier, OllamaChatProvider, GroqChatProvider
from core.dependency import CodeDependencyAnalyzer
from core.summarization import ElementSummarizer
from core.cache import PersistentSummaryCache
from core.content import relative_identifier
from core.pipeline import TracePipeline
from core import jobs
from core.auth import get_current_user_optional
from core.db.models import Project, ProjectSource, User
from core.db.session import SessionLocal, get_db
from core.projects import artifact_store, service
from core.projects.run_config import config_key
from core.sync import SyncError, fetch_source
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


def provider_for(side: ArtifactInput, role: str):
    """Provider for one side of a path-based request."""
    kind = ARTIFACT_KINDS_BY_KEY.get(side.kind)
    if kind is None:
        raise HTTPException(status_code=400, detail=f"Unknown artifact kind '{side.kind}'.")
    if role not in kind.roles:
        raise HTTPException(
            status_code=400,
            detail=f"'{kind.label}' artifacts cannot be the {role} of a trace.",
        )

    if side.text.strip():
        if not kind.accepts_text:
            raise HTTPException(
                status_code=400,
                detail=f"'{kind.label}' artifacts cannot be provided as pasted text.",
            )
        return TextProvider(side.text)

    if not side.path:
        raise HTTPException(
            status_code=400, detail=f"Give a path or text for the {role} artifact."
        )
    return build_provider(side.kind, side.path)


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
        case PreprocessorType.MODEL_UML:
            return ModelUmlPreprocessor()


def get_chat_provider():
    # provider = OllamaChatProvider() # Working perfectly
    return GroqChatProvider(api_keys=settings.groq_api_keys_list) # Also working


def get_classifier(classifier_type: ClassifierType, use_cache: bool):
    provider = get_chat_provider()
    match classifier_type:
        case ClassifierType.SIMPLE:
            return SimpleClassifier(provider=provider, use_persistent_cache=use_cache)
        case ClassifierType.REASONING:
            return ReasoningClassifier(provider=provider, use_persistent_cache=use_cache)


def get_summarizer(kind_key: str, enabled: bool, use_cache: bool) -> ElementSummarizer | None:
    """A summarizer for one side, or None when that side needs no summaries.

    Which kinds benefit is declared in the artifact registry, so a new type
    opts in there rather than here.
    """
    kind = ARTIFACT_KINDS_BY_KEY.get(kind_key)
    if not enabled or kind is None or not kind.summarize:
        return None

    provider = get_chat_provider()
    # Keyed by the model, so switching models does not mix two different
    # descriptions of the same code into one index.
    cache = PersistentSummaryCache(provider.model_name()) if use_cache else None
    return ElementSummarizer(provider=provider, cache=cache)


def get_project_id(request: AnalyzeRequest) -> str:
    if request.project_id:
        return sanitize_project_id(request.project_id)

    target_path = os.path.abspath(request.target.path or "project")
    basename = os.path.basename(target_path.rstrip("\\/")) or "project"
    digest = sha256(target_path.encode("utf-8")).hexdigest()[:10]
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
    source_kind: str,
    target_kind: str,
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
    summarize_elements: bool = request_default("summarize_elements"),
    # Last run's links for this configuration, so the classifier is asked about
    # them again rather than losing them to a shifted top-k. Only a sync has
    # any: a first run has no previous links, and a re-run reads the very same
    # files, so its retrieval cannot have shifted.
    pinned_links: dict[str, set[str]] | None = None,
    # The directories the providers read from, so pinned identifiers and this
    # run's elements can be compared in the same form.
    workspace_roots: list[Path] | None = None,
    on_progress=None,
) -> AnalyzeResponse:
    # Computed here rather than by each caller: every setting that goes into it
    # is already a parameter of this function, so there is one spelling of the
    # configuration and no way for two entry points to disagree about it.
    key = config_key(
        source_preprocessor=source_preprocessor,
        target_preprocessor=target_preprocessor,
        source_output_level=source_output_level,
        target_output_level=target_output_level,
        classifier=classifier,
        n_results=n_results,
        dependency_expansion_depth=dependency_expansion_depth,
        summarize_elements=summarize_elements,
    )

    pipeline = TracePipeline(
        source_provider=source_provider,
        target_provider=target_provider,
        source_preprocessor=get_preprocessor(source_preprocessor),
        target_preprocessor=get_preprocessor(target_preprocessor),
        source_kind=source_kind,
        target_kind=target_kind,
        config_key=key,
        # Only the sides whose artifacts are not prose; the rest get None.
        source_summarizer=get_summarizer(
            source_kind, summarize_elements, use_persistent_cache
        ),
        target_summarizer=get_summarizer(
            target_kind, summarize_elements, use_persistent_cache
        ),
        embedder=OllamaEmbeddingCreator(use_persistent_cache=use_persistent_cache),
        classifier=get_classifier(classifier, use_persistent_cache),
        chroma_path=chroma_path,
        n_results=n_results,
        source_output_level=source_output_level,
        target_output_level=target_output_level,
        # Expansion walks a call graph, so it only means anything when the
        # target really is source code. Any other kind ignores the setting
        # rather than running an analyzer that would find nothing.
        dependency_analyzer=(
            CodeDependencyAnalyzer()
            if target_kind == KIND_CODE and dependency_expansion_depth > 0
            else None
        ),
        dependency_expansion_depth=dependency_expansion_depth,
        reset_vector_stores=reset_vector_stores,
        pinned_links=pinned_links,
        workspace_roots=workspace_roots,
        on_progress=on_progress,
    )
    result = pipeline.run().to_dict()

    return AnalyzeResponse(
        trace_links=[TraceLinkResponse(**link) for link in result["trace_links"]],
        source_elements=[ElementResponse(**e) for e in result["source_elements"]],
        target_elements=[ElementResponse(**e) for e in result["target_elements"]],
        unimplemented=result["unimplemented"],
        summary=result["summary"],
        element_links=result["element_links"],
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
        source_provider=provider_for(request.source, ROLE_SOURCE),
        target_provider=provider_for(request.target, ROLE_TARGET),
        source_kind=request.source.kind,
        target_kind=request.target.kind,
        source_preprocessor=request.source_preprocessor,
        target_preprocessor=request.target_preprocessor,
        classifier=request.classifier,
        n_results=request.n_results,
        source_output_level=request.source_output_level,
        target_output_level=request.target_output_level,
        dependency_expansion_depth=request.dependency_expansion_depth,
        summarize_elements=request.summarize_elements,
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
    except HTTPException:
        # A rejected request is the caller's problem, not a server fault.
        raise
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


def side_manifest(
    artifacts: list[dict],
    role: str,
    source: ProjectSource | None = None,
    ref: str | None = None,
) -> dict:
    """Describe one side of the trace for later storage."""
    # The stored unit is the side, not the artifact: materialise_side writes a
    # whole side into one directory, which is what makes it a single corpus.
    names = [artifact.get("name") or artifact.get("id") or "artifact" for artifact in artifacts]
    entry = {
        "role": role,
        "artifact_type": artifacts[0]["kind"],
        "name": source.name if source else ", ".join(names)[:255],
        "directory": role,
    }
    if source is not None:
        # Names the row these files came from, so the run updates that source
        # rather than being filed as a new upload beside it - and records the
        # commit, which is what the next check compares against.
        entry["source_id"] = source.source_id
        entry["ref"] = ref
    return entry


def require_owned_project(db: Session, user: User | None, project_id: str | None) -> Project:
    """The project a connected source is being read from.

    Uploading needs no account, but fetching from a source does: the source
    belongs to a project, and reading it is reading whatever that project's
    stored credentials can reach.
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to run an analysis from a connected source.",
        )

    numeric = str(project_id or "").strip()
    if not numeric.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Running from a connected source needs the project it belongs to.",
        )

    project = service.get_project(db, user.user_id, int(numeric))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def side_source(
    db: Session, project: Project, artifacts: list[dict], role: str
) -> ProjectSource | None:
    """The connected source a side is taken from, if it is not uploaded.

    A side is one or the other. Half a codebase fetched and half uploaded is
    not a thing anyone means, and allowing it would leave the source recording
    a commit it does not actually hold.
    """
    named = [artifact for artifact in artifacts if artifact.get("source_id")]
    if not named:
        return None

    if len(named) != len(artifacts) or len({a["source_id"] for a in named}) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The {role} side must be either uploaded or taken from a single "
                f"connected source, not a mixture."
            ),
        )

    source = service.get_source(db, project, int(named[0]["source_id"]))
    if source is None:
        raise HTTPException(
            status_code=400,
            detail=f"No connected source matches the one chosen for the {role} side.",
        )
    if source.kind != artifacts[0]["kind"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{source.name}' supplies {source.kind}, which is not what the "
                f"{role} side is set to."
            ),
        )
    return source


def build_provider(kind_key: str, path):
    """Reads a directory of artifacts of one kind.

    Extend this alongside the ARTIFACT_KINDS registry when adding a new type.
    """
    match kind_key:
        case "requirements":
            return DocumentProvider(str(path))
        case "code":
            return CodeProvider(str(path))
        case "architecture":
            return ModelProvider(str(path))

    raise HTTPException(status_code=400, detail=f"No provider registered for '{kind_key}'.")


def relativize(identifier: str, roots: list[Path]) -> str:
    """Strip the temp workspace prefix so identifiers read as project paths."""
    # The pipeline reduces identifiers the same way when matching pinned links,
    # so both go through one implementation - two that drifted apart is what
    # made pinning silently match nothing.
    return relative_identifier(identifier, roots)


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
    # Stored to be matched against a later run's elements, and every run works
    # in a differently named temp directory - so an absolute path here would
    # never match again, and pinning would quietly do nothing forever.
    response.element_links = [
        (relativize(source, roots), relativize(target, roots))
        for source, target in response.element_links
    ]
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


@router.post("/analyze/upload", response_model=AnalysisStartResponse)
async def analyze_upload(
    background: BackgroundTasks,
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
    summarize_elements: bool = Form(request_default("summarize_elements")),
    analysis_mode: AnalysisMode = Form(request_default("analysis_mode")),
    project_id: str | None = Form(None),
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Start a run over browser-uploaded artifacts, and return a job to watch.

    The uploaded bytes are read here - an upload is a stream belonging to this
    request - and everything after that happens in the background, because a
    real run takes minutes and no browser waits that long.


    `artifacts` is a JSON array describing each side's material:
        [{"id","name","kind","file_indexes":[...]},   uploaded files
         {"id","name","kind","text"},                 pasted text
         {"id","name","kind","source_id": 4}]         a connected source

    `file_indexes` point into `files`, and `file_paths` carries each file's
    relative path so folder uploads keep their structure. An artifact naming a
    `source_id` is fetched from that source instead - which is how a codebase
    already on GitHub never has to be uploaded by hand.

    Each side takes a list of artifact ids. Several artifacts on one side are
    analysed together as a single corpus - that is how a set of loose code
    files becomes one codebase. Only the referenced artifacts are written to
    disk.

    Anonymous callers may upload; fetching from a connected source needs the
    signed-in owner of the project it belongs to.
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

    # A side may be taken from something the project is already connected to
    # rather than uploaded. That belongs to a project, so it needs the project
    # and its owner - neither of which an anonymous run has.
    project = None
    if any(a.get("source_id") for a in source_artifacts + target_artifacts):
        project = require_owned_project(db, user, project_id)
    side_sources = {
        ROLE_SOURCE: side_source(db, project, source_artifacts, ROLE_SOURCE) if project else None,
        ROLE_TARGET: side_source(db, project, target_artifacts, ROLE_TARGET) if project else None,
    }

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

    # The workspace outlives the request: the run happens after it, and saving
    # is a separate call that may not come for minutes. Anything nobody saves
    # is reaped on the retention window.
    artifact_store.purge_expired_uploads()
    upload_id, workspace = artifact_store.create_upload_dir()

    # Session runs used to share one directory, so two of them at once wiped
    # each other's vectors. Each gets its own now that runs outlive requests.
    if analysis_mode != AnalysisMode.PROJECT:
        chroma_path = get_chroma_path(f"session-{upload_id}")

    budget = UploadBudget()
    try:
        # Uploaded bytes are read here and nowhere else: an UploadFile is a
        # stream from this request and is gone once the response is sent.
        for role, side in ((ROLE_SOURCE, source_artifacts), (ROLE_TARGET, target_artifacts)):
            if side_sources[role] is None:
                materialise_side(side, files, paths, workspace / role, budget)
    except HTTPException:
        artifact_store.discard_upload(upload_id)
        raise

    plan = {
        "source_artifacts": source_artifacts,
        "target_artifacts": target_artifacts,
        # Ids, not rows: the background half opens its own session.
        "source_ids": {role: (s.source_id if s else None) for role, s in side_sources.items()},
        "source_preprocessor": source_preprocessor,
        "target_preprocessor": target_preprocessor,
        "source_output_level": source_output_level,
        "target_output_level": target_output_level,
        "classifier": classifier,
        "n_results": n_results,
        "dependency_expansion_depth": dependency_expansion_depth,
        "summarize_elements": summarize_elements,
        "chroma_path": chroma_path,
        "use_persistent_cache": use_persistent_cache,
        "reset_vector_stores": reset_vector_stores,
    }

    job = jobs.create_job(
        db,
        user.user_id if user else None,
        int(project_id) if str(project_id or "").isdigit() else None,
        jobs.KIND_ANALYSIS,
    )
    background.add_task(run_analysis_job, job.job_id, upload_id, plan)
    logger.info(f"Analysis job {job.job_id} filed, workspace {upload_id}")
    return AnalysisStartResponse(job_id=job.job_id, token=job.token, upload_id=upload_id)


def progress_writer(db: Session, job_id: int):
    """A progress callback that does not write to the database on every element.

    One row update per element would be a hundred commits for a run nobody is
    reading that fast. A second apart is more than enough to watch, and the
    final step of each stage always lands so the bar does not stop short.
    """
    last_written = 0.0

    def report(stage: str, current: int = 0, total: int = 0) -> None:
        nonlocal last_written
        now = time.monotonic()
        if current and current != total and now - last_written < 1.0:
            return
        last_written = now
        jobs.set_stage(db, job_id, stage, current, total)

    return report


def perform_analysis(db: Session, upload_id: str, plan: dict, job_id: int) -> AnalyzeResponse:
    """Fetch whatever was not uploaded, then run the pipeline over the lot."""
    workspace = artifact_store.upload_dir(upload_id)
    if workspace is None or not workspace.is_dir():
        raise SyncError("The uploaded files are no longer available.")

    refs = {}
    for role in (ROLE_SOURCE, ROLE_TARGET):
        source_id = plan["source_ids"][role]
        if source_id is None:
            continue
        source = db.get(ProjectSource, source_id)
        if source is None:
            raise SyncError("A connected source was removed before the run started.")
        jobs.set_stage(db, job_id, f"Fetching {source.name}")
        # The commit comes back so the saved run records what it analysed.
        refs[role] = fetch_source(source, workspace / role)

    source_artifacts = plan["source_artifacts"]
    target_artifacts = plan["target_artifacts"]
    artifact_store.write_manifest(workspace, [
        side_manifest(source_artifacts, ROLE_SOURCE,
                      db.get(ProjectSource, plan["source_ids"][ROLE_SOURCE])
                      if plan["source_ids"][ROLE_SOURCE] else None, refs.get(ROLE_SOURCE)),
        side_manifest(target_artifacts, ROLE_TARGET,
                      db.get(ProjectSource, plan["source_ids"][ROLE_TARGET])
                      if plan["source_ids"][ROLE_TARGET] else None, refs.get(ROLE_TARGET)),
    ])

    source_dir, target_dir = workspace / ROLE_SOURCE, workspace / ROLE_TARGET
    response = build_pipeline_response(
        source_provider=build_provider(source_artifacts[0]["kind"], source_dir),
        target_provider=build_provider(target_artifacts[0]["kind"], target_dir),
        source_kind=source_artifacts[0]["kind"],
        target_kind=target_artifacts[0]["kind"],
        source_preprocessor=plan["source_preprocessor"],
        target_preprocessor=plan["target_preprocessor"],
        classifier=plan["classifier"],
        n_results=plan["n_results"],
        source_output_level=plan["source_output_level"],
        target_output_level=plan["target_output_level"],
        dependency_expansion_depth=plan["dependency_expansion_depth"],
        summarize_elements=plan["summarize_elements"],
        chroma_path=plan["chroma_path"],
        use_persistent_cache=plan["use_persistent_cache"],
        reset_vector_stores=plan["reset_vector_stores"],
        on_progress=progress_writer(db, job_id),
    )
    response.upload_id = upload_id
    return relativize_response(response, [source_dir, target_dir])


def run_analysis_job(job_id: int, upload_id: str, plan: dict) -> None:
    """The background half of a run. Owns its own session.

    The request's session is closed by the time this runs - the response has
    already gone out - so nothing from it can be carried in here.
    """
    with SessionLocal() as db:
        jobs.start(db, job_id)
        try:
            response = perform_analysis(db, upload_id, plan, job_id)
            jobs.succeed(db, job_id, response.model_dump(mode="json"))
            logger.info(
                f"Analysis job {job_id} finished: {len(response.trace_links)} trace links"
            )
        except Exception as error:
            # The files go with it: nothing produced a result, so there is
            # nothing to save and nothing worth keeping on disk.
            artifact_store.discard_upload(upload_id)
            logger.error(f"Analysis job {job_id} failed: {error}", exc_info=True)
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            jobs.fail(db, job_id, str(detail))
