from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    groq_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    chroma_path: str = "./chroma_data"

    class Config:
        env_file = ".env"

settings = Settings()