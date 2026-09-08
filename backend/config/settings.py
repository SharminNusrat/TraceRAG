from pathlib import Path
from pydantic_settings import BaseSettings

# Relative paths in here are anchored to the backend package, never to the
# directory the server happened to be started from. Starting it from somewhere
# else would otherwise point at an empty artifact store while the database
# still described the files in the old one.
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    groq_api_keys: str = ""
    ollama_host: str = "http://localhost:11434"
    chroma_path: str = "./chroma_data"

    # A token belonging to the server itself. Used *only* to raise the rate
    # limit on repositories that are already public - never to reach a private
    # one, which would hand every signed-in user whatever this token can see.
    github_token: str = ""

    # The OAuth app users authorise, so each person connects with their own
    # GitHub account and TraceRAG can only reach what they granted. Leave
    # unset and the Connect GitHub button reports that it is not configured.
    github_client_id: str = ""
    github_client_secret: str = ""

    # Where to send the browser back to once GitHub has answered. The OAuth
    # callback lands on the API, not the app, so it has to know the way home.
    frontend_url: str = "http://localhost:5173"

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    # Application database: accounts, projects and saved analyses. No default:
    # a fallback would quietly open an empty database while the artifact store
    # on disk belonged to the real one, and the garbage collector reads that
    # database to decide what to keep.
    database_url: str = ""

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

    @property
    def storage_root(self) -> Path:
        """Where artifacts live, as an absolute path."""
        path = Path(self.storage_path)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    class Config:
        env_file = ".env"

settings = Settings()