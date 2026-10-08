"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root: .../S21 Reviewer/
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Validated runtime settings (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    encryption_key: str = Field(..., alias="ENCRYPTION_KEY")
    database_path: str = Field(default="data/bot.db", alias="DATABASE_PATH")
    poll_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=3600,
        alias="POLL_INTERVAL_SECONDS",
    )
    school_id: str = Field(
        default="bad03b39-ffd4-4217-9d24-65535fe1f293",
        alias="SCHOOL_ID",
    )
    auth_url: str = Field(
        default=(
            "https://auth.21-school.ru/auth/realms/EduPowerKeycloak"
            "/protocol/openid-connect/token"
        ),
        alias="AUTH_URL",
    )
    graphql_url: str = Field(
        default="https://platform.21-school.ru/services/graphql",
        alias="GRAPHQL_URL",
    )
    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite file."""
        path = Path(self.database_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()  # type: ignore[call-arg]
