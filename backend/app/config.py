"""Configuration for the service.

Everything is supplied by environment variables, with defaults that work for a
local checkout. Nothing here reaches outside the repository, and nothing fails
at import time -- `Settings` is built lazily by `get_settings()` so that
importing the app (for tests, for a liveness probe, for the ingest CLI's
--help) never requires a Gemini key.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

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
    # How long the generation model reasons before answering. "auto" sends no
    # setting and lets the model decide. Measured on gemini-3.8-flash over the
    # four golden prompts: auto spent 4k-21k hidden thinking tokens and took
    # 39-162s; "low" took 11-16s, and every script still compiled and ran
    # against the seed data in Postgres. "low" is the default. If the model
    # the alias points to rejects the setting, generation falls back to auto
    # (see app/adapters/gemini.py) rather than failing.
    generation_thinking_level: Literal["auto", "low", "medium", "high"] = "low"
    # The same for the request classifier. Measured over 23 scenarios x 3 runs
    # (on/off-topic, follow-ups, typos, Hinglish, prompt injection): auto and
    # low gave identical answers on every run; low cut the median from 3.9s
    # to 2.4s and the worst case from 17.4s to 5.7s.
    classifier_thinking_level: Literal["auto", "low", "medium", "high"] = "low"

    # --- adapters (app/adapters) -------------------------------------------
    # Which implementation backs each seam. One of each exists today; these
    # are where a second one would be selected.
    llm_provider: Literal["gemini"] = "gemini"
    vector_store: Literal["chroma"] = "chroma"
    schema_source: Literal["sqlite"] = "sqlite"

    # --- retrieval -------------------------------------------------------
    retrieval_top_k: int = Field(default=5, ge=1, le=25)
    schema_collection: str = "schema_chunks"
    pattern_collection: str = "pattern_templates"

    # --- paths -----------------------------------------------------------
    reference_db_path: Path = BACKEND_DIR / "db" / "reference.db"
    schema_metadata_path: Path = BACKEND_DIR / "db" / "metadata.json"
    sp_templates_dir: Path = APP_DIR / "sp_templates"
    vector_store_dir: Path = BACKEND_DIR / "data" / "vector_store"

    # --- upstream timeouts and retries -----------------------------------
    # Generation takes 11-16s at thinking level "low". 200s is headroom for
    # "auto" and for the fallback when a model rejects the level: those took
    # 39-162s on the golden prompts (one earlier outlier reached 232s, and
    # would now time out). The classifier and embeddings are short calls
    # (~2.5s and <1s).
    classifier_timeout_s: float = Field(default=20, gt=0)
    embedding_timeout_s: float = Field(default=20, gt=0)
    generation_timeout_s: float = Field(default=200, gt=0)
    # Attempts include the first call. Short calls use the client library's
    # own retry (408/429/5xx and timeouts). Generation does not: that retry
    # also fires after a timeout, which would turn one 200s timeout into
    # several. Generation retries only fast 429/5xx failures (see generator).
    classifier_attempts: int = Field(default=3, ge=1, le=5)
    embedding_attempts: int = Field(default=3, ge=1, le=5)
    generation_attempts: int = Field(default=2, ge=1, le=3)
    # A generation attempt that failed after running longer than this is
    # never retried, whatever the error: it has already spent the budget.
    generation_retry_window_s: float = Field(default=30, gt=0)
    # Wait before retry n is n times this.
    generation_retry_backoff_s: float = Field(default=2, ge=0)

    # --- request limits ----------------------------------------------------
    # Per user message.
    max_prompt_chars: int = Field(default=8000, ge=1)
    # The caller resends the whole conversation, including every script the
    # service returned (~8 KB each), so the body cap sizes how long a
    # conversation can get: 256 KB is roughly 25 turns.
    max_request_bytes: int = Field(default=256 * 1024, ge=1024)

    # --- rate limiting (in-process fallback; see app/ratelimit.py) -------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: float = Field(default=6, gt=0)
    rate_limit_burst: int = Field(default=3, ge=1)
    # How many trusted proxies sit in front of the service. Each appends the
    # address it received the request from to X-Forwarded-For, so with N
    # proxies the client is the Nth entry from the end; anything before that
    # is whatever the client sent and cannot be trusted. 0 (the default) uses
    # the TCP peer and ignores the header. Behind an ALB alone (the AWS
    # deployment in infra/): 1. Behind a CDN and then an ALB: 2.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    # --- streaming ---------------------------------------------------------
    # An SSE comment is sent this often while the model is thinking, so the
    # connection is never idle long enough for a load balancer to drop it.
    sse_heartbeat_s: float = Field(default=15, gt=0)

    # --- logging -----------------------------------------------------------
    log_level: str = "INFO"

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

    @field_validator("generation_thinking_level", "classifier_thinking_level", mode="before")
    @classmethod
    def _lower_thinking_level(cls, value: object) -> object:
        """GENERATION_THINKING_LEVEL=LOW and =low are the same setting."""
        return value.strip().lower() if isinstance(value, str) else value

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
