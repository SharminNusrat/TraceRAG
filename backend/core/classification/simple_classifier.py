import re
from typing import Optional
from core.schemas import Element
from core.classification.base import Classifier, ClassificationResult
from core.classification.prompts import SimplePromptTemplate, format_prompt
from core.classification.chat_provider import ChatProvider
from core.classification.ollama_chat_provider import OllamaChatProvider

class SimpleClassifier(Classifier):

    def __init__(self, provider: Optional[ChatProvider] = None, template: str = None): 
        self.provider = provider or OllamaChatProvider()
        self.template = template or SimplePromptTemplate.DEFAULT.value
        self._cache: dict[tuple, bool] = {}

    def classify(self, source: Element, target_candidates: list[tuple[Element, float]]) -> list[ClassificationResult]:
        results = []
        for target, similarity in target_candidates:
            linked = self._is_linked(source, target)
            if linked:
                results.append(ClassificationResult(
                    source=source,
                    target=target,
                    confidence=similarity,
                    explanation=None
                ))
        return results

    def _is_linked(self, source: Element, target: Element) -> bool:
        cache_key = (source.identifier, target.identifier)
        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt = format_prompt(
            template=self.template,
            source_type=source.type,
            source_content=source.content,
            target_type=target.type,
            target_content=target.content
        )
        response = self.provider.chat(prompt)
        linked = self._parse_response(response)
        self._cache[cache_key] = linked
        return linked

    def _parse_response(self, response: str) -> bool:
        cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
        return "yes" in cleaned.lower()
    