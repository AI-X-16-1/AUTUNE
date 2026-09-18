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

    secret_key: str = "local-development-only-change-me-in-every-environment"
    """JWT signing key. Must be overridden outside local; see the validator below."""

    encryption_key: str = ""
    """Fernet key for integration credentials at rest (``autune_core.crypto``).

    Empty is allowed: a deployment that never stores a team's token never needs
    it, and failing at first use gives a better message than failing at import.
    Required outside local — see the validator below.
    """

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""
    """Socket-mode token, local development only."""

    retention_days: int = 90
    """Analysis results are deleted after this many days.
    See docs/architecture/privacy.md section 4."""

    cors_allowed_origins: str = ""
    """Comma-separated origins apps/api sends Access-Control-Allow-Origin for.

    Empty means no CORS headers at all — the default, and what every
    environment gets until this is set explicitly. A browser blocks
    cross-origin responses on its own; only a local dev setup running
    apps/web and apps/api as separate origins (e.g. :3000 and :8000) needs
    this, and only for those exact origins, e.g.
    ``http://localhost:3000``."""

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

    @model_validator(mode="after")
    def _require_encryption_key_outside_local(self) -> Settings:
        """Team credentials are stored encrypted, so staging and production need a key.

        Checked at startup rather than at the first Notion call, because
        discovering it there means a team has already tried to connect.
        """
        if self.env != "local" and not self.encryption_key:
            raise ValueError(
                f"AUTUNE_ENCRYPTION_KEY is not set in env={self.env}; integration "
                "credentials cannot be stored. Generate one: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        return self

    @model_validator(mode="after")
    def _reject_open_cors_outside_local(self) -> Settings:
        """A wildcard or plain-http origin outside local defeats the allowlist.

        Local dev is the one case ``http://localhost:3000`` is legitimate; any
        other environment serving the browser client is expected to be on
        https, and ``*`` is never a real allowlist entry.
        """
        if self.env == "local":
            return self
        for origin in (o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()):
            if origin == "*" or not origin.startswith("https://"):
                raise ValueError(
                    f"AUTUNE_CORS_ALLOWED_ORIGINS contains {origin!r} in env={self.env}; "
                    "outside local, every origin must be an explicit https:// URL, never '*'."
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
