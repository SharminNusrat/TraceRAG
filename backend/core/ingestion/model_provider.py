import os
from core.schemas import Artifact, ArtifactType
from core.ingestion.base import ArtifactProvider

class ModelProvider(ArtifactProvider):

    # Deliberately not .xml: it identifies no particular content, so walking a
    # folder would pull in build files and configs as if they were models.
    SUPPORTED_EXTENSIONS = {".uml", ".xmi"}

    def __init__(self, path: str, artifact_type: ArtifactType = ArtifactType.ARCHITECTURE_MODEL):
        self.path = path
        self.artifact_type = artifact_type

    def load(self) -> list[Artifact]:
        if os.path.isdir(self.path):
            return self._load_from_folder()
        elif os.path.isfile(self.path):
            return [self._load_file(self.path, os.path.dirname(self.path))]
        raise ValueError(f"Path does not exist: {self.path}")

    def _load_from_folder(self) -> list[Artifact]:
        artifacts = []
        for root, _, files in os.walk(self.path):
            for filename in sorted(files):
                ext = os.path.splitext(filename)[1].lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    artifacts.append(self._load_file(os.path.join(root, filename), self.path))
        return artifacts

    def _load_file(self, file_path: str, base: str) -> Artifact:
        # The model is handed on as raw XML; the preprocessor parses it.
        with open(file_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        return Artifact(
            identifier=self._make_identifier(file_path, base),
            type=self.artifact_type,
            content=content
        )

    def _make_identifier(self, file_path: str, base: str) -> str:
        """Path relative to the load root, extension included - as the other
        providers do, so two models of different formats cannot collide."""
        try:
            relative = os.path.relpath(file_path, base) if base else os.path.basename(file_path)
        except ValueError:  # different drive on Windows
            relative = os.path.basename(file_path)
        return relative.replace(os.sep, "/")
