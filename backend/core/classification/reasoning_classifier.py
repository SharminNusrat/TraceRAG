import re
from typing import Optional
from core.cache import namespace_for
from core.schemas import Element
from core.classification.base import Classifier, Verdict, untagged_verdict
from core.classification.prompts import ReasoningPromptTemplate, format_prompt
from core.classification.chat_provider import ChatProvider
from core.classification.ollama_chat_provider import OllamaChatProvider

class ReasoningClassifier(Classifier):

    def __init__(
        self,
        provider: Optional[ChatProvider] = None,
        user_template: str = None,
        system_message: str = None,
        use_system_message: bool = True,
        use_persistent_cache: bool = False,
    ):
        self.provider = provider or OllamaChatProvider()
        self.user_template = user_template or ReasoningPromptTemplate.DEFAULT_USER.value
        self.system_message = system_message or ReasoningPromptTemplate.DEFAULT_SYSTEM.value
        self.use_system_message = use_system_message
        # Both halves of the prompt go into the namespace: the system message
        # is what asks for the explanation, so changing it changes the answer.
        super().__init__(
            namespace_for(
                self.provider.model_name(), "reasoning", self.user_template, self.system_message
            )
            if use_persistent_cache else None
        )

    def _ask(self, source: Element, target: Element) -> Verdict:
        prompt = format_prompt(
            template=self.user_template,
            source_type=source.type,
            source_content=source.content,
            target_type=target.type,
            target_content=target.content
        )
        system = self.system_message if self.use_system_message else None
        response = self.provider.chat(prompt, system_message=system)
        return self._parse_response(response)

    def _parse_response(self, response: str) -> Verdict:
        cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()

        trace_match = re.search(r'<trace>(yes|no)</trace>', cleaned, re.IGNORECASE)
        linked = trace_match.group(1).lower() == "yes" if trace_match else untagged_verdict(cleaned)

        explanation = None
        explanation_match = re.search(r'<explanation>(.*?)</explanation>', cleaned, re.DOTALL)
        if explanation_match:
            explanation = explanation_match.group(1).strip()

        return linked, explanation
