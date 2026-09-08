"""Module A settings. Environment prefix ``AUTUNE_AUDIO_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AudioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTUNE_AUDIO_", extra="ignore")

    whisper_model: str = "large-v3"
    device: str = "cpu"
    temp_dir: str = "/tmp/autune-audio"


@lru_cache
def get_settings() -> AudioSettings:
    return AudioSettings()
