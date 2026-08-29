"""
utils/config.py
---------------
Typed settings loaded from the project .env file via pydantic-settings.
"""

from __future__ import annotations

from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration values for GraphHarvestor.

    Values are read from environment variables (or a .env file in the
    working directory) and validated at import time.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")

    # ── Google OAuth ─────────────────────────────────────────────────────────
    google_credentials_json: Path = Field(
        default=Path("credentials/google_client_secret.json"),
        alias="GOOGLE_CREDENTIALS_JSON",
    )

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="", alias="NEO4J_PASSWORD")

    # ── General ──────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return v


# Module-level singleton — import this everywhere.
settings = Settings()
