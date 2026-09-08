"""Module C settings. Environment prefix ``AUTUNE_GAP_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class GapSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_GAP_", extra="ignore")

    risk_threshold: float = 0.7


@lru_cache
def get_settings() -> GapSettings:
    return GapSettings()
