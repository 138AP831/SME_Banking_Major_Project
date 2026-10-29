"""Application configuration (environment driven, no secrets in code)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        extra="ignore",
    )

    app_name: str = "SMEFlow Intelligence"
    environment: str = "DEMO ENVIRONMENT"

    # Persistence (SQLite for the prototype; any SQLAlchemy URL, e.g. PostgreSQL, works)
    database_url: str = f"sqlite:///{DATA_DIR / 'smeflow.db'}"
    checkpoint_db_path: str = str(DATA_DIR / "checkpoints.sqlite")
    models_dir: str = str(DATA_DIR / "models")
    upload_dir: str = str(DATA_DIR / "uploads")
    sample_docs_dir: str = str(DATA_DIR / "sample_documents")

    # Demo behaviour
    # Empty string => anchor the synthetic data to "today" when the DB is seeded.
    demo_as_of_date: str = ""
    seed_rng: int = 20261029
    # Human-readable pacing between workflow nodes (events are still emitted by the real
    # execution lifecycle; this only slows the run down so a viewer can follow it). 0 = off.
    demo_step_delay_ms: int = 450
    bootstrap_workflows: bool = True

    # CORS: only the dev origins that need to reach the API
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )

    # Optional LLM provider. Everything works without it.
    llm_provider: Literal["none", "anthropic", "openai"] = "none"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_model: str = ""
    llm_timeout_s: float = 20.0


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for p in (DATA_DIR, Path(s.models_dir), Path(s.upload_dir), Path(s.sample_docs_dir)):
        p.mkdir(parents=True, exist_ok=True)
    return s
