from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    groq_api_keys: str = ""
    ollama_host: str = "http://localhost:11434"
    chroma_path: str = "./chroma_data"

    @property
    def groq_api_keys_list(self) -> list[str]:
        return [key.strip() for key in self.groq_api_keys.split(",") if key.strip()]

    class Config:
        env_file = ".env"

settings = Settings()