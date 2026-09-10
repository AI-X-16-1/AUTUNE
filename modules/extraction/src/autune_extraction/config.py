"""Module B settings. Environment prefix ``AUTUNE_EXTRACTION_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
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

    classifier_checkpoint: str = ""
    """Pinned, and recorded with every classification. Never a floating tag.

    **Blank by default, because no trained checkpoint exists yet** (#10). The
    name this used to default to, ``team-autune/deberta-v3-ko-utterance-5way``,
    was never published -- the hub answers 401 for it. A default that cannot
    load fails on the first meeting a worker picks up, with a hub error that
    reads like a network problem.

    Blank makes ``local`` and ``hosted`` refuse in the registry instead, naming
    this variable. Point it at a directory ``python -m
    autune_extraction.training`` wrote, or at a hub revision once one is
    published. ``fake`` needs none."""

    classifier_endpoint: str = ""
    """Our own inference server, required when ``classifier_impl=hosted``."""

    classifier_device: str = "cpu"
    """``cpu`` or ``cuda``, for ``classifier_impl=local``. Mirrors
    ``AUTUNE_AUDIO_DEVICE``.

    Defaulting to CPU rather than to whatever the machine has: a worker that
    silently picks a GPU is a worker whose throughput changes when it is
    rescheduled, and a latency measured on one scheduling says nothing about the
    other.

    This module classifies **every utterance of every meeting**, the heaviest
    inference in the product, so the setting matters more here than in module A,
    which runs its model once per recording.
    """

    @model_validator(mode="after")
    def _device_is_known(self) -> ExtractionSettings:
        """A typo should not surface as a CUDA error in the middle of a meeting."""
        if self.classifier_device not in ("cpu", "cuda"):
            raise ValueError(
                f"AUTUNE_EXTRACTION_CLASSIFIER_DEVICE={self.classifier_device!r}; "
                "expected 'cpu' or 'cuda'"
            )
        return self


@lru_cache
def get_settings() -> ExtractionSettings:
    return ExtractionSettings()
