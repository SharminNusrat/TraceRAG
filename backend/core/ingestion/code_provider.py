import os
from core.schemas import Artifact, ArtifactType
from core.ingestion.base import ArtifactProvider

class CodeProvider(ArtifactProvider):

    SUPPORTED_EXTENSIONS = {".js", ".ts", ".py", ".java"}
    IGNORED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "dist", "build"}

    def __init__(self, folder_path: str):
        self.folder_path = folder_path

    def load(self) -> list[Artifact]:
        artifacts = []
        for root, dirs, files in os.walk(self.folder_path):
            dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]
            for file in files:
                if self._is_supported(file):
                    file_path = os.path.join(root, file)
                    content = self._read_file(file_path)
                    if content:
                        artifacts.append(Artifact(
                            identifier=file_path,
                            type=ArtifactType.SOURCE_CODE,
                            content=content
                        ))
        return artifacts

    def _is_supported(self, filename: str) -> bool:
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.SUPPORTED_EXTENSIONS

    def _read_file(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()