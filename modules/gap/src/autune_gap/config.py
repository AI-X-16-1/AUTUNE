"""Module C settings. Environment prefix ``AUTUNE_GAP_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class GapSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(env_prefix="AUTUNE_GAP_", env_file=".env", extra="ignore")

    risk_threshold: float = 0.7


@lru_cache
def get_settings() -> GapSettings:
    return GapSettings()
