import re
from typing import Optional
from core.cache import namespace_for
from core.schemas import Element
from core.classification.base import Classifier, Verdict
from core.classification.prompts import SimplePromptTemplate, format_prompt
from core.classification.chat_provider import ChatProvider
from core.classification.ollama_chat_provider import OllamaChatProvider

class SimpleClassifier(Classifier):

    def __init__(
        self,
        provider: Optional[ChatProvider] = None,
        template: str = None,
        use_persistent_cache: bool = False,
    ):
        self.provider = provider or OllamaChatProvider()
        self.template = template or SimplePromptTemplate.DEFAULT.value
        # The namespace covers the prompt as well as the model, so editing the
        # template retires its answers rather than reusing them.
        super().__init__(
            namespace_for(self.provider.model_name(), "simple", self.template)
            if use_persistent_cache else None
        )

    def _ask(self, source: Element, target: Element) -> Verdict:
        prompt = format_prompt(
            template=self.template,
            source_type=source.type,
            source_content=source.content,
            target_type=target.type,
            target_content=target.content
        )
        response = self.provider.chat(prompt)
        # This prompt asks for a verdict and forbids anything else, so there is
        # never an explanation to carry back.
        return self._parse_response(response), None

    def _parse_response(self, response: str) -> bool:
        cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()

        # The prompt asks for the tag, so read the tag. Searching the reply for
        # the word "yes" instead turns any model that reasons out loud into a
        # yes: "no, though one might say yes" would count as a link.
        trace_match = re.search(r'<trace>(yes|no)</trace>', cleaned, re.IGNORECASE)
        if trace_match:
            return trace_match.group(1).lower() == "yes"

        # No tag at all, so there is nothing better to go on than the prose.
        return "yes" in cleaned.lower()
