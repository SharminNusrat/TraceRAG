import ollama
from dataclasses import dataclass

DEFAULT_SEED = 133742243
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MODEL = "deepseek-r1:7b"
DEFAULT_HOST = "http://localhost:11434"

@dataclass
class OllamaChatProvider:
    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    seed: int = DEFAULT_SEED
    temperature: float = DEFAULT_TEMPERATURE

    def chat(self, prompt: str, system_message: str = None) -> str:
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        response = ollama.chat(
            model=self.model,
            messages=messages,
            options={
                "seed": self.seed,
                "temperature": self.temperature
            }
        )
        return response["message"]["content"]

    def model_name(self) -> str:
        return self.model