"""Where a run's data lives, and how its identifiers are made to read as project paths."""

import os
import re
from hashlib import sha256
from pathlib import Path

from api.schemas import AnalyzeRequest, AnalyzeResponse
from core.content import relative_identifier

PROJECT_DATA_ROOT = Path("./chroma_data/projects")


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
