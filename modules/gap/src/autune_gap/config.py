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
    """A gap at or above this scores ``high``, and only ``high`` is surfaced by
    default. Precision, not recall — see docs/modules/gap.md, "Metric"."""

    ner_impl: str = "spacy"
    """Which entity extractor to run: ``spacy`` or ``fake``.

    No ``external``. Extraction runs over every utterance of a meeting, so
    sending it to somebody else's model is a decision about where personal data
    goes rather than a config value — see ``pipeline.base``."""

    ner_model: str = "ko_core_news_lg"
    """Pinned, and recorded with the rows it produces. Never a floating tag.

    A general Korean pipeline trained on written text; meeting speech is spoken
    Korean, so recall is expected to be poor until #13 fine-tunes one."""


@lru_cache
def get_settings() -> GapSettings:
    return GapSettings()
