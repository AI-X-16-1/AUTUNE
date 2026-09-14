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

    gap_classifier_impl: str = "local"
    """Which gap-pattern classifier to run: ``local`` or ``fake``.

    No ``external`` and no ``hosted``. A cross-meeting classifier is exactly
    the aggregation ``privacy.md`` section 3 asks this module to be careful
    with — see ``pipeline.base``."""

    gap_classifier_backbone: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    """The sentence-embedding backbone SetFit fits its few-shot head onto, for
    ``gap_classifier_impl=local``. Multilingual because meeting titles are
    Korean (``docs/product/glossary.md``) but ``Gap.category`` in C's fixture
    is English (`packages/contracts/.../fixtures/gap_report.json`) — nothing
    guarantees C settles on one language before this is built.

    A name, not a pinned revision — ``SetFitGapClassifier.model_version``
    doesn't need a hub commit hash the way module B's checkpoint does, because
    nothing here is fine-tuned and redistributed; the head is refit from
    ``pipeline.classifier._SEED_EXAMPLES`` every process start."""


@lru_cache
def get_settings() -> IntelligenceSettings:
    return IntelligenceSettings()
