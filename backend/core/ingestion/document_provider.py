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
            return self._load_file(self.path)
        raise ValueError(f"Path does not exist: {self.path}")

    def _load_from_folder(self) -> list[Artifact]:
        artifacts = []
        for filename in os.listdir(self.path):
            ext = os.path.splitext(filename)[1].lower()
            if ext in self.SUPPORTED_EXTENSIONS:
                file_path = os.path.join(self.path, filename)
                artifacts += self._load_file(file_path)
        return artifacts

    def _load_file(self, file_path: str) -> list[Artifact]:
        ext = os.path.splitext(file_path)[1].lower()
        match ext:
            case ".txt":
                return self._load_txt(file_path)
            case ".pdf":
                return self._load_pdf(file_path)
            case ".docx":
                return self._load_docx(file_path)
            case _:
                raise ValueError(f"Unsupported file type: {ext}")

    def _load_txt(self, file_path: str) -> list[Artifact]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return [Artifact(
            identifier=os.path.splitext(os.path.basename(file_path))[0],
            type=self.artifact_type,
            content=content
        )]

    def _load_pdf(self, file_path: str) -> list[Artifact]:
        doc = fitz.open(file_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()
        return [Artifact(
            identifier=file_path,
            type=self.artifact_type,
            content=full_text
        )]

    def _load_docx(self, file_path: str) -> list[Artifact]:
        doc = docx.Document(file_path)
        full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        return [Artifact(
            identifier=file_path,
            type=self.artifact_type,
            content=full_text
        )]