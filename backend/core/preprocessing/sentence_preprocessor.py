import nltk
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor

nltk.download('punkt_tab', quiet=True)

class SentencePreprocessor(Preprocessor):

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            artifact_element = Element(
                identifier=artifact.identifier,
                type=artifact.type,
                content=artifact.content,
                granularity=0,
                parent_id=None,
                compare=False
            )
            elements.append(artifact_element)

            sentences = nltk.sent_tokenize(artifact.content)
            for idx, sentence in enumerate(sentences):
                if sentence.strip():  # Only add non-empty sentences
                    sentence_element = Element(
                        identifier=f"{artifact.identifier}::sentence_{idx}",
                        type=artifact.type,
                        content=sentence.strip(),
                        granularity=1,
                        parent_id=artifact.identifier,
                        compare=True
                    )
                    elements.append(sentence_element)
        return elements