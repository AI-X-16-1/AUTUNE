"""Module B settings. Environment prefix ``AUTUNE_EXTRACTION_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ExtractionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_EXTRACTION_", extra="ignore")

    pass


@lru_cache
def get_settings() -> ExtractionSettings:
    return ExtractionSettings()
