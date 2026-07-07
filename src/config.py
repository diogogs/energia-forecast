"""Application settings, loaded from environment variables / the local .env file.

Secrets never live in code. `.env` is git-ignored; CI and deploys inject the same
names as environment variables. Keep this in sync with `.env.example`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, sourced from env vars (case-insensitive)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    entsoe_api_token: str = ""

    # Neon Postgres. Pooled for app/ingestion; direct (unpooled) for alembic; RO for serving.
    database_url: str = ""
    database_url_direct: str = ""
    database_url_ro: str = ""

    # DagsHub-hosted MLflow tracking.
    mlflow_tracking_uri: str = ""
    mlflow_tracking_username: str = ""
    mlflow_tracking_password: str = ""

    api_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
