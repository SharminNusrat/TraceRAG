from pathlib import Path
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    groq_api_keys: str = ""
    ollama_host: str = "http://localhost:11434"
    chroma_path: str = "./chroma_data"

    # A token belonging to the server itself. 
    github_token: str = ""

    # The OAuth app users authorise, so each person connects with their own
    # GitHub account and TraceRAG can only reach what they granted.
    github_client_id: str = ""
    github_client_secret: str = ""

    # Where to send the browser back to once GitHub has answered. 
    frontend_url: str = "http://localhost:5173"

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    # Application database: accounts, projects and saved analyses. 
    database_url: str = ""

    # Where uploaded artifacts live once an analysis is saved.
    storage_path: str = "./app_data/artifacts"
    # An upload nobody saved is abandoned work.
    upload_retention_hours: int = 24

    # Signing key for access tokens. Leaving unset in development and a throwaway
    # key is generated per process; will set it in .env before deploying, otherwise
    # every restart signs users out.
    secret_key: str = ""
    access_token_expire_minutes: int = 60 * 24 * 7  # one week

    @property
    def groq_api_keys_list(self) -> list[str]:
        return [key.strip() for key in self.groq_api_keys.split(",") if key.strip()]

    @property
    def storage_root(self) -> Path:
        """Where artifacts live, as an absolute path."""
        path = Path(self.storage_path)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    class Config:
        env_file = ".env"

settings = Settings()