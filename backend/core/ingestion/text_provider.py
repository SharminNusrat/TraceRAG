from core.schemas import Artifact, ArtifactType
from core.ingestion.base import ArtifactProvider


class TextProvider(ArtifactProvider):

    def __init__(self, raw_text: str, artifact_type: ArtifactType = ArtifactType.REQUIREMENT):
        self.raw_text = raw_text
        self.artifact_type = artifact_type

    def load(self) -> list[Artifact]:
        return [Artifact(
            identifier="raw_text",
            type=self.artifact_type,
            content=self.raw_text
        )]