import re
import logging
from core.schemas import Artifact, Element
from core.preprocessing.base import Preprocessor
from core.preprocessing.sentence_preprocessor import SentencePreprocessor

logger = logging.getLogger(__name__)

class SectionPreprocessor(Preprocessor):

    HEADING_PATTERN = re.compile(r'^(\d+(\.\d+)*)\.?\s+(.+)', re.MULTILINE)

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
                        content=f"{section_number} {section_title}\n{section_content.strip()}",
                        granularity=granularity,
                        parent_id=parent_id,
                        compare=True
                    )
                    logger.info(f"ELEMENT id={section_element.identifier} | granularity={section_element.granularity} | parent={section_element.parent_id}")
                    logger.info(f"CONTENT:\n{section_element.content}\n{'='*50}")
                    elements.append(section_element)
        return elements

    def _split_sections(self, text: str) -> list[tuple[str, str, str]]:
        matches = list(self.HEADING_PATTERN.finditer(text))
        sections = []
        for i, match in enumerate(matches):
            section_number = match.group(1)
            section_title = match.group(3).strip()
            start = match.end()
            end = self._find_section_end(matches, i, section_number, len(text))
            section_content = text[start:end].strip()
            sections.append((section_number, section_title, section_content))
        return sections

    def _find_section_end(self, matches: list,  current_index: int, current_number: str, text_length: int) -> int:
        current_level = len(current_number.split('.'))
        for j in range(current_index + 1, len(matches)):
            next_number = matches[j].group(1)
            next_level = len(next_number.split('.'))
            if next_level <= current_level:
                return matches[j].start()
        return text_length

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