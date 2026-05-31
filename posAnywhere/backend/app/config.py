"""Application configuration.

Centralises every tunable value in one typed Settings object that is loaded
from environment variables (and the local .env file). Importing `settings`
anywhere in the codebase gives access to validated configuration.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from the environment."""

    # General
    app_name: str = "posAnywhere.io"

    # Persistence: PostgreSQL is REQUIRED at runtime. Override via DATABASE_URL.
    # (The automated test suite overrides this with an isolated SQLite file.)
    database_url: str = "postgresql+psycopg2://posanywhere:posanywhere@localhost:5432/posanywhere"

    # CORS: comma-separated list of allowed browser origins ("*" allows all).
    cors_origins: str = "*"

    # Dispatch engine tuning knobs (see modules/dispatch.py).
    average_driver_speed_kmh: float = 25.0
    default_prep_minutes: int = 15

    # Authentication / JWT. SECRET_KEY MUST be overridden in production with a
    # long random value (e.g. `openssl rand -hex 32`); the default is insecure.
    secret_key: str = "dev-insecure-change-me-please-set-SECRET_KEY"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Logging. LOG_LEVEL is one of DEBUG/INFO/WARNING/ERROR/CRITICAL. Set
    # LOG_FILE to also write a rotating log file (empty = console/stdout only).
    # LOG_FORMAT is "text" (human-readable) or "json" (for log aggregators).
    log_level: str = "INFO"
    log_file: str = ""
    log_format: str = "text"

    # Tell pydantic-settings to read variables from a .env file if present.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS origins as a clean Python list for FastAPI middleware."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed only once per process)."""
    return Settings()


# Convenient module-level singleton used throughout the app.
settings = get_settings()
