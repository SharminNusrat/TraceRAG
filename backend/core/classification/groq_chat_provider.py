import itertools
import logging
from groq import Groq, RateLimitError
from core.classification.chat_provider import ChatProvider

logger = logging.getLogger(__name__)

# llama-3.1-8b-instant and llama-3.3-70b-versatile were both decommissioned on
# 16 August 2026; this is Groq's like-for-like replacement for the 70b.
# openai/gpt-oss-20b is the same shape and faster, if throughput matters more.
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_TEMPERATURE = 0.0

class GroqChatProvider(ChatProvider):

    def __init__(self, api_keys: list[str], model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE):
        self.api_keys = api_keys
        self.model = model
        self.temperature = temperature
        self._key_cycle = itertools.cycle(api_keys)
        # self.current_key = next(self._key_cycle)
        self.current_key = None
        self.client = None

    def chat(self, prompt: str, system_message: str = None) -> str:
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        attempts = 0
        while attempts < len(self.api_keys):
            self._rotate_key()  # rotate before every attempt/request

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature
                )
                return response.choices[0].message.content
            except RateLimitError:
                logger.warning(f"Rate limit on key {attempts + 1}/{len(self.api_keys)}, rotating")
                attempts += 1

        raise RuntimeError("All API keys have hit their rate limits.")

    def _rotate_key(self):
        self.current_key = next(self._key_cycle)
        self.client = Groq(api_key=self.current_key)

    def model_name(self) -> str:
        return self.model