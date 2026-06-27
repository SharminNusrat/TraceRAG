from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor

EXTENSION_TO_LANGUAGE = {
    '.py': Language.PYTHON,
    '.js': Language.JS,
    '.ts': Language.TS,
    '.java': Language.JAVA
}

class CodeChunkingPreprocessor(Preprocessor):

    DEFAULT_CHUNK_SIZE = 500

    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.chunk_size = chunk_size

    def preprocess(self, artifacts: list[Artifact]) -> list[Element]:
        elements = []
        for artifact in artifacts:
            element = Element(
                identifier=artifact.identifier,
                type=artifact.type,
                content=artifact.content,
                granularity=0,
                parent_id=None,
                compare=False
            )
            elements.append(element)

            chunks = self._split_code(artifact)
            for i, chunk in enumerate(chunks):
                if chunk.strip():  # Only add non-empty chunks
                    chunk_element = Element(
                        identifier=f"{artifact.identifier}::chunk_{i}",
                        type=artifact.type,
                        content=chunk.strip(),
                        granularity=1,
                        parent_id=artifact.identifier,
                        compare=True
                    )
                    elements.append(chunk_element)
        return elements

    def _split_code(self, artifact: Artifact) -> list[str]:
        extension = self._get_extension(artifact.identifier)
        language = EXTENSION_TO_LANGUAGE.get(extension)
        if language:
            splitter = RecursiveCharacterTextSplitter.from_language(
                language=language,
                chunk_size=self.chunk_size,
                chunk_overlap=200
            )
        else:
            splitter = RecursiveCharacterTextSplitter(chunk_size=self.chunk_size, chunk_overlap=200)
        return splitter.split_text(artifact.content)

    def _get_extension(self, identifier: str) -> str:
        dot_index = identifier.rfind('.')
        return identifier[dot_index:].lower() if dot_index != -1 else ''