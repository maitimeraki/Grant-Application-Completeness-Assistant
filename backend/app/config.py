from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'app.db'}"
    data_dir: Path = BACKEND_DIR / "data"
    storage_dir: Path = BACKEND_DIR / "storage"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = ""
    seed_on_startup: bool = False
    samples_dir: Path = BACKEND_DIR.parent / "samples"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    app_version: str = "0.1.0"
    app_name: str = "Grant Application Completeness Assistant"
    log_level: str = "INFO"

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_postgres_scheme(cls, value: object) -> object:
        # Render may supply postgres://...; SQLAlchemy only accepts postgresql://
        if isinstance(value, str) and value.startswith("postgres://"):
            return "postgresql://" + value[len("postgres://") :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
