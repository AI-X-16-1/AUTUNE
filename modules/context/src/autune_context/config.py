"""Module D settings. Environment prefix ``AUTUNE_CONTEXT_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(env_prefix="AUTUNE_CONTEXT_", env_file=".env", extra="ignore")

    rerank_top_k: int = 10


@lru_cache
def get_settings() -> ContextSettings:
    return ContextSettings()
