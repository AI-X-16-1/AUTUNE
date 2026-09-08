"""Module D settings. Environment prefix ``AUTUNE_CONTEXT_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_CONTEXT_", extra="ignore")

    rerank_top_k: int = 10


@lru_cache
def get_settings() -> ContextSettings:
    return ContextSettings()
