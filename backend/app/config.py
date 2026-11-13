"""Configuration for the service.

Everything is supplied by environment variables, with defaults that work for a
local checkout. Nothing here reaches outside the repository, and nothing fails
at import time -- `Settings` is built lazily by `get_settings()` so that
importing the app (for tests, for a liveness probe, for the ingest CLI's
--help) never requires a Gemini key.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

# Fixed by the pattern corpus on disk (app/sp_templates/<category>.sql), so this
# is a code-level constant rather than configuration.
PATTERN_CATEGORIES = [
    "pre_validation",
    "temp_tables",
    "backup",
    "main_script",
    "post_validation",
    "rollback_auto",
    "rollback_manual",
]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unusable."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # A .env at the repo root is a developer convenience. In a container
        # there is no .env and values come from the environment directly.
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- credentials -----------------------------------------------------
    gemini_api_key: str = Field(
        default="",
        description="Google AI Studio API key. Required for generation, embeddings and ingest.",
    )

    # --- models ----------------------------------------------------------
    # Deliberately floating aliases: gemini-2.0-flash and text-embedding-004
    # both returned 404 on this key, so the current-stable aliases are used.
    generation_model: str = "models/gemini-flash-latest"
    embedding_model: str = "models/gemini-embedding-001"

    # --- retrieval -------------------------------------------------------
    retrieval_top_k: int = Field(default=5, ge=1, le=25)
    schema_collection: str = "schema_chunks"
    pattern_collection: str = "pattern_templates"

    # --- paths -----------------------------------------------------------
    reference_db_path: Path = BACKEND_DIR / "db" / "reference.db"
    schema_metadata_path: Path = BACKEND_DIR / "db" / "metadata.json"
    sp_templates_dir: Path = APP_DIR / "sp_templates"
    data_dir: Path = BACKEND_DIR / "data"
    vector_store_dir: Path = BACKEND_DIR / "data" / "vector_store"
    app_db_path: Path = BACKEND_DIR / "data" / "app.db"

    # --- http ------------------------------------------------------------
    # Never "*". An empty list means no browser origin is allowed, which is the
    # right default for a service that normally sits behind a gateway.
    # NoDecode stops pydantic-settings from JSON-parsing the raw env value, so
    # the validator below can accept a plain comma-separated string instead of
    # requiring '["http://a","http://b"]'.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string so the env var stays readable:
        CORS_ALLOW_ORIGINS=http://localhost:5173,https://example.com
        """
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def require_gemini_api_key(self) -> str:
        """Returns the key, or raises with an actionable message.

        Called at the point of use rather than at import, so the process can
        start, serve /health, and report an honest /ready without a key.
        """
        if not self.gemini_api_key:
            raise ConfigError(
                "GEMINI_API_KEY is not set. Set it in the environment, or add it to a "
                f".env file at {REPO_ROOT}. Get a key at https://aistudio.google.com/apikey"
            )
        return self.gemini_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, built on first use and cached.

    Tests that need different values set the corresponding environment
    variables and call `get_settings.cache_clear()`.
    """
    return Settings()
