import ollama
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor

class SummarizePreprocessor(Preprocessor):

    DEFAULT_TEMPLATE = "Summarize the following {type}: {content}"

    def __init__(self, model: str = "llama3.1:8b", template: str = None):
        self.model = model
        self.template = template or self.DEFAULT_TEMPLATE

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            summary = self._summarize(artifact)
            element = Element(
                identifier=artifact.identifier,
                type=f"Summary of '{artifact.type}'",
                content=summary,
                granularity=0,
                parent_id=None,
                compare=True
            )
            elements.append(element)
        return elements
    
    def _summarize(self, artifact: Artifact) -> str:
        prompt = self.template.replace("{type}", artifact.type).replace("{content}", artifact.content)
        response = ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}]
        )
        return response["message"]["content"]