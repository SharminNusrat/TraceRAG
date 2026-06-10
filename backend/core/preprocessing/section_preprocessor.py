import re
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor
from core.preprocessing.sentence_preprocessor import SentencePreprocessor

class SectionPreprocessor(Preprocessor):

    HEADING_PATTERN = re.compile(r'^(\d+(\.\d+)*)\s+(.+)', re.MULTILINE)

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

            sections = self._split_sections(artifact.content)

            if not sections:
                elements += self._fallback(artifact)
            else:
                for section_number, section_title, section_content in sections:
                    granularity = self._get_granularity(section_number)
                    parent_id = self._get_parent_id(artifact.identifier, section_number, sections)
                    section_element = Element(
                        identifier=f"{artifact.identifier}::{section_number}",
                        type=artifact.type,
                        content=f"{section_number} {section_title}\n{section_content}",
                        granularity=granularity,
                        parent_id=parent_id,
                        compare=True
                    )
                    elements.append(section_element)
        return elements

    def _split_sections(self, text: str) -> list[tuple[str, str, str]]:
        matches = self.HEADING_PATTERN.finditer(text)
        sections = []
        for i, match in enumerate(matches):
            section_number = match.group(1)
            section_title = match.group(3).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section_content = text[start:end].strip()
            sections.append((section_number, section_title, section_content))
        return sections

    def _get_granularity(self, section_number: str) -> int:
        return section_number.count('.') + 1

    def _get_parent_id(self, artifact_id: str, section_number: str, sections: list[tuple]) -> str:
        parts = section_number.split('.')
        if len(parts) == 1:
            return artifact_id  # Top-level section's parent is the artifact itself
        parent_number = '.'.join(parts[:-1])
        for section_number, _, _ in sections:
            if section_number == parent_number:
                return f"{artifact_id}::{parent_number}"
        return artifact_id  # Fallback to artifact if parent section not found 

    def _fallback(self, artifact: Artifact) -> Element: 
        return SentencePreprocessor()._to_sentence_elements(artifact)