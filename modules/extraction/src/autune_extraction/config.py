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
    """Which classifier to run: ``local``, ``hosted``, ``fake``, ``llm`` or
    ``llm_checked``.

    ``llm`` sends utterance text as module A masked it -- nothing else -- to a
    cloud LLM (``pipeline.llm``). A name said aloud is not masked, so it goes too.
    It is never the default: where a meeting's text may go is a privacy
    decision, and the team signs it off before it is enabled outside a demo --
    see ``pipeline.base``.

    ``llm_checked`` sends the same as ``llm`` and has the local DeBERTa
    (``classifier_checkpoint``, required) check its commitments: one only the
    LLM found becomes a candidate instead of being asserted
    (``pipeline.checked``). It shows candidates only once
    ``candidate_confidence`` is set."""

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

    llm_api_key: str = ""
    """Provider API key for ``classifier_impl=llm``. Sent as a header, never in a
    body or URL. Blank makes the registry refuse ``llm`` by name.

    The code cannot tell a free-tier key from a paid one. A free tier may let the
    provider keep what it is sent, so a free key is for dummy meetings only
    (#392)."""

    llm_model: str = "gemini-3.8-flash"
    """The model ``classifier_impl=llm`` calls; every classification records
    ``llm:<model>+<fallback>`` (``llm:<model>`` with the fallback off). 3.8
    Flash found all 14 action items in the 8.txt comparison (2026-09-28); 3.5
    Flash-Lite found 12 at a seventh of the cost."""

    llm_fallback_model: str = "gemini-3.5-flash-lite"
    """Answers a window when ``llm_model`` stays unavailable (429/5xx/timeout after
    its retries). 3.8 Flash returned 503 three times running on 2026-09-28;
    Flash-Lite scored commitment F1 0.909 on 8.txt against 3.8 Flash's 0.968;
    with the worked examples now in the prompt, 0.938 there and 0.959 on a
    second dummy meeting. On a free-tier key it answers most windows: 3.8 Flash
    allows 5 requests a minute and 20 a day. Blank disables the fallback."""

    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    """The provider's API root for ``classifier_impl=llm``."""

    llm_timeout_sec: float = 60.0
    """Timeout per request (connect and read) for ``classifier_impl=llm``. A
    thinking model took 12-20 s a window against the real API; the shared
    client's 10 s made every window time out and retry. A window may retry and
    fall back, so it can take several of these."""

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
    ``fake``. No ``external``: step 4 reads a commitment or ambiguous
    utterance's own text, so an external implementation is the privacy.md
    section 6 question ``classifier_impl=llm`` is waiting on (#392).

    Mirrors ``classifier_impl``'s ``local``/``hosted``/``fake`` rather than
    module D's own ``AUTUNE_CONTEXT_NLI_*`` naming -- module D is a different
    module (modules never import each other) and this module's own classifier
    config is the closer precedent to stay consistent with.
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

    ``classifier_impl=llm_checked`` is the exception, because its confidences
    are not probabilities: 0.9 means the LLM and DeBERTa both said commitment,
    0.5 that only the LLM did (``pipeline.checked``). Any value in (0.5, 0.9]
    -- 0.7, say -- separates the two, and nothing is being estimated.
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

    embedder_impl: str = "fake"
    """Which embedder backs the resolver's similarity check: ``local``,
    ``hosted`` or ``fake`` (#175, #366). No ``hosted`` yet -- see
    ``pipeline.embedder``.

    Defaults to ``fake`` for the same reason ``resolver_impl`` does: this is a
    supplementary check the resolver already works without
    (``resolver_min_similarity`` unset has the same effect), and nothing turns
    it on until there is a measured threshold to turn it on with."""

    embedder_checkpoint: str = "nlpai-lab/KURE-v1"
    """Not blank by default, unlike ``resolver_checkpoint``: KURE-v1 is not a
    candidate awaiting evaluation, it is module D's already-shipped choice for
    "does this sentence mean the same thing as that one" in Korean, and this
    setting only matters once ``embedder_impl=local`` and
    ``resolver_min_similarity`` are both set regardless."""

    embedder_device: str = "cpu"
    """``cpu`` or ``cuda``, for ``embedder_impl=local``."""

    resolver_min_similarity: float | None = Field(default=None, ge=-1, le=1)
    """Below this cosine similarity to every line in its own context window, a
    resolved sentence is treated as ungrounded and the raw quote is kept
    instead.

    **Empty by default, and that is the point** -- the same reasoning as
    ``candidate_confidence`` and module D's ``link_confidence_threshold``: no
    embedding model has been run against a labelled set of good and bad
    resolutions yet, so there is no honest number to enforce. Unset, the
    resolver's groundedness check is exactly what it was before this setting
    existed -- the digit and named-person checks in ``pipeline.resolver``,
    unaffected by ``embedder_impl``."""

    dev_routes: bool = False
    """Mount the local-only page for connecting Notion by hand (``dev/``, #401).

    Needed on top of ``AUTUNE_ENV=local``: that env is also the default, so a
    deployment that forgot to set it would otherwise serve an unauthenticated
    route that stores any team's Notion token (lsh2217, review of #402). Off
    unless someone asks for it by name."""

    @field_validator("resolver_min_similarity", mode="before")
    @classmethod
    def _blank_similarity_means_unset(cls, value: object) -> object:
        """Same reason as ``candidate_confidence``: ``.env.example`` carries
        the name with no value, and a blank string is "deliberately not
        chosen," not a parse error."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _device_is_known(self) -> ExtractionSettings:
        """A typo should not surface as a CUDA error in the middle of a meeting."""
        for name, value in (
            ("CLASSIFIER_DEVICE", self.classifier_device),
            ("NLI_DEVICE", self.nli_device),
            ("RESOLVER_DEVICE", self.resolver_device),
            ("EMBEDDER_DEVICE", self.embedder_device),
        ):
            if value not in ("cpu", "cuda"):
                raise ValueError(f"AUTUNE_EXTRACTION_{name}={value!r}; expected 'cpu' or 'cuda'")
        return self


@lru_cache
def get_settings() -> ExtractionSettings:
    return ExtractionSettings()
