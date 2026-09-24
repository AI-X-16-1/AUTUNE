"""Module B settings. Environment prefix ``AUTUNE_EXTRACTION_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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
    published. ``fake`` needs none.

    Several checkpoints separated by commas make ``local`` an ensemble -- seeds of
    one training run, sharing a vocabulary (checked on load). The first scores
    every utterance; the others are asked only where it is unsure, and those
    answers are averaged. ``hosted`` refuses a list. See
    ``pipeline.classifier.ENSEMBLE_SEPARATOR`` and ``ESCALATE_BELOW`` for why and
    at what cost."""

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

    nli_impl: str = "local"
    """Which NLI model to run for step 4 (#12): ``local``, ``hosted`` or
    ``fake``. No ``external`` -- same reasoning as ``classifier_impl``.

    Mirrors ``classifier_impl``'s three-way shape rather than module D's own
    ``AUTUNE_CONTEXT_NLI_*`` naming -- module D is a different module (modules
    never import each other) and this module's own classifier config is the
    closer precedent to stay consistent with.
    """

    nli_checkpoint: str = ""
    """Pinned, and recorded as the classification's model version once NLI
    verifies it. Blank by default, the same reason ``classifier_checkpoint``
    is: no checkpoint is baked in here as a silent default, even though #172
    settled on one (klue/roberta-base fine-tuned on KorNLI) -- point this at
    it explicitly. ``fake`` needs none."""

    nli_endpoint: str = ""
    """Our own inference server, required when ``nli_impl=hosted``."""

    nli_device: str = "cpu"
    """``cpu`` or ``cuda``, for ``nli_impl=local``. Mirrors
    ``classifier_device``."""

    candidate_confidence: float | None = Field(default=None, ge=0, le=1)
    """Below this confidence an item is shown as a candidate rather than asserted.

    ADR 0006 ranks recall above precision -- a wrong item costs a click, a
    missing one costs re-reading the meeting -- so low-confidence items are kept
    and marked rather than dropped.

    **Empty by default, and that is the point.** The number has to come from the
    classifier's confidence distribution over the evaluation set (#10), which
    does not exist yet. Until it does there is no honest threshold, so nothing is
    a candidate. A default picked to make the band look populated would be a
    number nobody measured, printed to the user as though somebody had.
    """

    @field_validator("candidate_confidence", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        """An empty environment variable is "no threshold", not a parse error.

        ``.env.example`` carries the name with no value, because that is how a
        setting says "deliberately not chosen" to whoever opens the file. Without
        this, ``cp .env.example .env`` -- the documented first run -- makes
        ``get_settings()`` raise on every call, and ``read_model`` calls it for
        every action item read.

        Every other blank in ``.env.example`` happens to be a ``str`` field,
        where "" parses fine. This is the first one that is not, so nothing
        caught it before. CI does not either: there is no ``.env`` there.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    resolver_impl: str = "fake"
    """Which reference resolver to run: ``local``, ``hosted`` or ``fake`` (#175).

    Defaults to ``fake`` rather than ``local``, unlike the classifier: #175's own
    model choice (``Qwen/Qwen3-4B-Instruct-2507``, a candidate) is not yet
    confirmed by the Korean judgment run the issue asks for, so nothing runs it
    by default. ``fake`` returns each commitment's raw quote unchanged -- the
    same output ``build_action_items`` produced before #175 existed."""

    resolver_checkpoint: str = ""
    """A local model path or hub id for ``resolver_impl=local``, the model
    version recorded with every resolution for ``resolver_impl=hosted``. Blank
    makes both refuse in the registry, the same shape as ``classifier_checkpoint``."""

    resolver_endpoint: str = ""
    """Our own inference server, required when ``resolver_impl=hosted``."""

    resolver_device: str = "cpu"
    """``cpu`` or ``cuda``, for ``resolver_impl=local``. Mirrors
    ``classifier_device``."""

    @model_validator(mode="after")
    def _device_is_known(self) -> ExtractionSettings:
        """A typo should not surface as a CUDA error in the middle of a meeting."""
        for name, value in (
            ("CLASSIFIER_DEVICE", self.classifier_device),
            ("NLI_DEVICE", self.nli_device),
            ("RESOLVER_DEVICE", self.resolver_device),
        ):
            if value not in ("cpu", "cuda"):
                raise ValueError(f"AUTUNE_EXTRACTION_{name}={value!r}; expected 'cpu' or 'cuda'")
        return self


@lru_cache
def get_settings() -> ExtractionSettings:
    return ExtractionSettings()
