"""Configuration. Every value comes from the environment, never a literal.

Module settings use the prefix ``AUTUNE_<MODULE>_``. Document each new variable
in docs/engineering/environments.md and add it to .env.example.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_", env_file=".env", extra="ignore")

    env: Environment = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://autune:autune@localhost:5432/autune"
    redis_url: str = "redis://localhost:6379/0"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "autunedev"

    chroma_url: str = "http://localhost:8001"

    secret_key: str = "local-development-only-change-me-in-every-environment"
    """JWT signing key. Must be overridden outside local; see the validator below."""

    retention_days: int = 90
    """Analysis results are deleted after this many days.
    See docs/architecture/privacy.md section 4."""

    @model_validator(mode="after")
    def _reject_default_secret_outside_local(self) -> Settings:
        """A shipped default signing key forges any user's session.

        Failing at startup is far cheaper than discovering this in production.
        """
        if self.env != "local" and self.secret_key.startswith("local-development-only"):
            raise ValueError(
                f"AUTUNE_SECRET_KEY still holds the development default in env={self.env}. "
                'Generate one: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
