from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor

class ArtifactPreprocessor(Preprocessor):

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            element = Element(
                identifier=artifact.identifier,
                type=artifact.type,
                content=artifact.content,
                granularity=0,
                parent_id=None,
                compare=True
            )
            elements.append(element)
        return elements