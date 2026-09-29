"""The agent layer's settings, read from ``AUTUNE_AGENT_*``."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_AGENT_", env_file=".env", extra="ignore")

    router_impl: Literal["gemini", "off"] = "gemini"
    """``off`` answers every chat with a refusal and calls nothing -- the layer
    switched off, with the fixed pipeline untouched (agent-layer.md section 2)."""

    llm_api_key: str = ""
    """Empty means the chat endpoint refuses with a configuration error naming
    this variable, rather than guessing."""
    llm_model: str = "gemini-3.5-flash-lite"
    """Routing and composing are short calls. Flash-Lite keeps 3.8 Flash's small
    free-tier quota for module B, which hit it (#419)."""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_timeout_sec: float = 30.0


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
