import re
from typing import Optional
from core.schemas import Element
from core.classification.base import Classifier, ClassificationResult
from core.classification.prompts import ReasoningPromptTemplate, format_prompt
from core.classification.chat_provider import ChatProvider
from core.classification.ollama_chat_provider import OllamaChatProvider

class ReasoningClassifier(Classifier):

    def __init__(self, provider: Optional[ChatProvider] = None, user_template: str = None, system_message: str = None, use_system_message: bool = True):
        self.provider = provider or OllamaChatProvider()
        self.user_template = user_template or ReasoningPromptTemplate.DEFAULT_USER.value
        self.system_message = system_message or ReasoningPromptTemplate.DEFAULT_SYSTEM.value
        self.use_system_message = use_system_message
        self._cache: dict[tuple, tuple[bool, str | None]] = {}

    def classify(self, source: Element, target_candidates: list[tuple[Element, float]]) -> list[ClassificationResult]:
        results = []
        for target, similarity in target_candidates:
            linked, explanation = self._is_linked(source, target)
            if linked:
                results.append(ClassificationResult(
                    source=source,
                    target=target,
                    confidence=similarity,
                    explanation=explanation
                ))
        return results

    def _is_linked(self, source: Element, target: Element) -> tuple[bool, str | None]:
        cache_key = (source.identifier, target.identifier)
        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt = format_prompt(
            template=self.user_template,
            source_type=source.type,
            source_content=source.content,
            target_type=target.type,
            target_content=target.content
        )
        system = self.system_message if self.use_system_message else None
        response = self.provider.chat(prompt, system_message=system)
        linked, explanation = self._parse_response(response)
        self._cache[cache_key] = (linked, explanation)
        return linked, explanation

    def _parse_response(self, response: str) -> tuple[bool, str | None]:
        cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()

        trace_match = re.search(r'<trace>(yes|no)</trace>', cleaned, re.IGNORECASE)
        # linked = trace_match and trace_match.group(1).lower() == "yes" if trace_match else "yes" in cleaned.lower()
        linked = trace_match.group(1).lower() == "yes" if trace_match else "yes" in cleaned.lower()

        explanation = None
        explanation_match = re.search(r'<explanation>(.*?)</explanation>', cleaned, re.DOTALL)
        if explanation_match:
            explanation = explanation_match.group(1).strip()

        return linked, explanation