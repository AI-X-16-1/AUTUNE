"""Module B settings. Environment prefix ``AUTUNE_EXTRACTION_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ExtractionSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(
        env_prefix="AUTUNE_EXTRACTION_", env_file=".env", extra="ignore"
    )

    classifier_impl: str = "local"
    """Which classifier to run: ``local``, ``hosted`` or ``fake``.

    No ``external``. Sending a meeting's utterances to somebody else's classifier
    is a decision about where personal data goes, not a config value -- see
    ``pipeline.base``."""

    classifier_checkpoint: str = "team-autune/deberta-v3-ko-utterance-5way"
    """Pinned, and recorded with every classification. Never a floating tag."""

    classifier_endpoint: str = ""
    """Our own inference server, required when ``classifier_impl=hosted``."""


@lru_cache
def get_settings() -> ExtractionSettings:
    return ExtractionSettings()
