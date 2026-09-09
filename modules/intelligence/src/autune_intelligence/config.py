"""Module E settings. Environment prefix ``AUTUNE_INTELLIGENCE_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IntelligenceSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(
        env_prefix="AUTUNE_INTELLIGENCE_", env_file=".env", extra="ignore"
    )

    aggregate_timeout_seconds: int = 600
    """How long E waits after the first of B/C/D reports before aggregating
    without the rest. The timeout is anchored to the first arrival, so the
    slowest module gets the least slack — and B is the heaviest (per-utterance
    LLM calls). Revisit this number once B's pipeline has real timings (#10)."""


@lru_cache
def get_settings() -> IntelligenceSettings:
    return IntelligenceSettings()
