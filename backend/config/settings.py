from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    groq_api_keys: str = ""
    ollama_host: str = "http://localhost:11434"
    chroma_path: str = "./chroma_data"

    # Application database: accounts, projects and saved analyses.
    database_url: str = "sqlite:///./app_data/tracerag.db"

    # Where uploaded artifacts live once an analysis is saved.
    storage_path: str = "./app_data/artifacts"
    # An upload nobody saved is abandoned work. Kept briefly so a visitor can
    # still sign up and save after the run, then reaped.
    upload_retention_hours: int = 24

    # Signing key for access tokens. Leave unset in development and a throwaway
    # key is generated per process; set it in .env before deploying, otherwise
    # every restart signs users out.
    secret_key: str = ""
    access_token_expire_minutes: int = 60 * 24 * 7  # one week

    @property
    def groq_api_keys_list(self) -> list[str]:
        return [key.strip() for key in self.groq_api_keys.split(",") if key.strip()]

    class Config:
        env_file = ".env"

settings = Settings()