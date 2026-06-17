from groq import Groq
from core.classification.chat_provider import ChatProvider

DEFAULT_MODEL = "llama-3.1-8b-instant"
DEFAULT_TEMPERATURE = 0.0

class GroqChatProvider(ChatProvider):

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE):
        self.client = Groq(api_key=api_key)
        self.model = model
        self.temperature = temperature

    def chat(self, prompt: str, system_message: str = None) -> str:
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature
        )
        return response.choices[0].message.content

    def model_name(self) -> str:
        return self.model