import os
import fitz, docx
from core.schemas import Artifact, ArtifactType
from core.ingestion.base import ArtifactProvider

class DocumentProvider(ArtifactProvider):

    SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}

    def __init__(self, path: str, artifact_type: ArtifactType = ArtifactType.REQUIREMENT):
        self.path = path
        self.artifact_type = artifact_type

    def load(self) -> list[Artifact]:
        if os.path.isdir(self.path):
            return self._load_from_folder()
        elif os.path.isfile(self.path):
            return self._load_file(self.path, os.path.dirname(self.path))
        raise ValueError(f"Path does not exist: {self.path}")

    def _load_from_folder(self) -> list[Artifact]:
        """Walk the folder recursively - uploaded document folders keep their
        directory structure, so a flat listing would silently miss nested files."""
        artifacts = []
        for root, _, files in os.walk(self.path):
            for filename in sorted(files):
                ext = os.path.splitext(filename)[1].lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    artifacts += self._load_file(os.path.join(root, filename), self.path)
        return artifacts

    def _make_identifier(self, file_path: str, base: str) -> str:
        """Path relative to the load root, extension included.

        The extension is part of the identity: preprocessors select a parser by
        reading the extension off the identifier, so stripping it would break
        anything that has to re-derive an element's file type. Relative (rather
        than absolute) keeps temp upload paths out of the results and separates
        same-named files living in different subfolders.
        """
        try:
            relative = os.path.relpath(file_path, base) if base else os.path.basename(file_path)
        except ValueError:  # different drive on Windows
            relative = os.path.basename(file_path)
        return relative.replace(os.sep, "/")

    def _load_file(self, file_path: str, base: str = "") -> list[Artifact]:
        ext = os.path.splitext(file_path)[1].lower()
        identifier = self._make_identifier(file_path, base)
        match ext:
            case ".txt":
                return self._load_txt(file_path, identifier)
            case ".pdf":
                return self._load_pdf(file_path, identifier)
            case ".docx":
                return self._load_docx(file_path, identifier)
            case _:
                raise ValueError(f"Unsupported file type: {ext}")

    def _load_txt(self, file_path: str, identifier: str) -> list[Artifact]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return [Artifact(
            identifier=identifier,
            type=self.artifact_type,
            content=content
        )]

    def _load_pdf(self, file_path: str, identifier: str) -> list[Artifact]:
        doc = fitz.open(file_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()
        return [Artifact(
            identifier=identifier,
            type=self.artifact_type,
            content=full_text
        )]

    def _load_docx(self, file_path: str, identifier: str) -> list[Artifact]:
        doc = docx.Document(file_path)
        full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        return [Artifact(
            identifier=identifier,
            type=self.artifact_type,
            content=full_text
        )]