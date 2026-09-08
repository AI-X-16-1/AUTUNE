"""Module E settings. Environment prefix ``AUTUNE_INTELLIGENCE_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IntelligenceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_INTELLIGENCE_", extra="ignore")

    aggregate_timeout_seconds: int = 600


@lru_cache
def get_settings() -> IntelligenceSettings:
    return IntelligenceSettings()
