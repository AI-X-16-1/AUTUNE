"""Module B settings. Environment prefix ``AUTUNE_EXTRACTION_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CLOUD_IMPLS = frozenset({"llm", "llm_checked"})
"""The implementation names that send utterance text to a cloud provider."""


class ExtractionSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    # hide_input_in_errors: a validator below can refuse the whole object, and
    # pydantic's message would otherwise print the input it refused -- the
    # raw environment, the provider key in it.
    model_config = SettingsConfigDict(
        env_prefix="AUTUNE_EXTRACTION_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    classifier_impl: str = "local"
    """Which classifier to run: ``local``, ``hosted``, ``fake``, ``llm`` or
    ``llm_checked``.

    ``llm`` sends utterance text as module A masked it -- nothing else -- to a
    cloud LLM (``pipeline.llm``), with the names on the meeting team's roster
    replaced first (#411). A name that is not on the roster goes as it was said.
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

    llm_api_key: SecretStr = SecretStr("")
    """Provider API key for ``classifier_impl=llm``. Sent as a header, never in a
    body or URL. Blank here, the deployment's shared key below is used; blank
    in both makes the registry refuse ``llm`` by name.

    A ``SecretStr``, like module D's: a settings object that is printed,
    logged or dumped shows ``**********`` and not the key (review of #701).
    ``pipeline.registry`` is the one place that takes the value out, to hand
    it to the client that sends it.

    The code cannot tell a free-tier key from a paid one. A free tier may let the
    provider keep what it is sent, so a free key is for dummy meetings only
    (#392)."""

    shared_llm_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="AUTUNE_LLM_API_KEY"
    )
    """``AUTUNE_LLM_API_KEY``: a provider key under a name with no module in it,
    which is the name the team's deployment secret has. Read only when
    ``AUTUNE_EXTRACTION_LLM_API_KEY`` is blank -- blank, not just unset, since
    ``.env.example`` ships that line empty -- so a key given to B alone still
    wins. ``llm_api_key`` is what the rest of B reads; this only fills it.

    **A key selects nothing.** Whether B calls a provider at all is still
    ``classifier_impl`` and ``resolver_impl``, never the default, so a
    deployment that sets this for another module does not send B's speech
    anywhere. The free-tier rule above holds for this key too (#392).

    B is the only module that reads this name today: C, D and the agent read
    their own (``AUTUNE_GAP_VERIFIER_API_KEY``, ``AUTUNE_CONTEXT_LLM_API_KEY``,
    ``AUTUNE_AGENT_LLM_API_KEY``), and each is its owner's to change.

    **It has to be a key for the provider ``llm_base_url`` points at** --
    Google's Generative Language API by default. The URL picks where the
    text goes, not the key: another provider's key under this name sends the
    masked text to Google first and fails authentication there."""

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

    due_reminders: bool = False
    """``AUTUNE_EXTRACTION_DUE_REMINDERS``: whether an item's assignee is sent a
    Slack DM the day before its due date and after it passes (``reminders``).
    **Off by default** -- a DM to a person is something a deployment turns on,
    not something it has to remember to stop. ``true`` turns it on, and even
    then it sends only where a team connected Slack and the assignee linked
    their account."""

    weekly_digest: bool = False
    """``AUTUNE_EXTRACTION_WEEKLY_DIGEST``: whether each person is sent, on
    Monday in Korea, a Slack DM listing their own open action items
    (``reminders.build_weekly_digest``, the user, 2026-10-04). Off by default
    for the reason ``due_reminders`` is; sends only where a team connected
    Slack and the person linked their account.
    """

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

    llm_acknowledged_392: bool = False
    """``AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392``: the second switch a cloud
    implementation needs (#392, proposed by module A's owner in review of #405).

    "Demo meetings only" and "a paid key for real ones" are both rules the
    code cannot check: nothing marks a meeting as a dummy, and nothing says
    which tier a key is. What the code can do is make sending speech to a
    provider something a deployment says twice. With ``classifier_impl`` set
    to ``llm`` or ``llm_checked``, or ``resolver_impl`` or ``summary_impl``
    set to ``llm``, and this not true, these settings refuse to load.

    **It turns nothing on.** Set alone, it changes nothing.

    **Not keyed on ``AUTUNE_ENV``.** ``.env.example`` ships
    ``AUTUNE_ENV=local``, so a deployment made from that file would be the
    one let through. (The variable's own default has been ``production``
    since #446; an earlier version of this text said ``local``.)

    **Deleting this flag is the migration** once #392 is decided: every place
    that set it is a ``grep`` away."""

    resolver_impl: str = "fake"
    """Which reference resolver to run: ``local``, ``hosted``, ``llm`` or ``fake``
    (#175). ``llm`` is a cloud model, opt-in the way ``classifier_impl=llm`` is,
    and replaces the team's roster names before it sends (#530).

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

    resolver_model: str = "gemini-3.5-flash-lite"
    """The model ``resolver_impl=llm`` asks first. Its own setting, not
    ``llm_model``: the classifier wants the model that finds every commitment,
    and this wants the cheap one -- a rewrite of one sentence -- with a second
    model behind it."""

    resolver_second_model: str = "gemini-3.8-flash"
    """Asked once for a request when ``resolver_model``'s answer fails a check
    (a clause of its own, the deadline dropped, a runaway length), and instead of
    it when it stays unavailable. Blank turns both off: a failed answer is then
    the raw quote. On a free-tier key this model allows 5 requests a minute and
    20 a day, so it is meant for the few answers that need it."""

    resolver_device: str = "cpu"
    """``cpu`` or ``cuda``, for ``resolver_impl=local``. Mirrors
    ``classifier_device``."""

    summary_impl: str = "none"
    """``none`` or ``llm``: whether a meeting gets a summary written by a cloud
    model on the 요약 tab (#421 v2). ``llm`` sends the meeting's consented,
    masked lines out, the team's names replaced, so it is opt-in and needs
    ``llm_acknowledged_392`` like every other cloud setting. ``none`` leaves
    the tab as v1 built it, from B's own rows."""

    summary_model: str = "gemini-3.5-flash-lite"
    """The model ``summary_impl=llm`` asks. The cheap one: a meeting takes a
    call per section and one to combine them."""

    summary_fallback_model: str = "gemini-3.8-flash"
    """Asked instead when ``summary_model`` stays unavailable. Blank disables it."""

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

    Needed on top of ``AUTUNE_ENV=local``. Since #446 a deployment that forgets
    ``AUTUNE_ENV`` is ``production``, so that is no longer the reason; this is:
    ``local`` is what every checkout, ``scripts/up.sh`` and the demo stack set,
    including a demo machine other people can reach, and ``local`` alone should
    not serve an unauthenticated route that stores any team's Notion token
    (lsh2217, review of #402). Off unless someone asks for it by name."""

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
    def _own_key_then_the_shared_one(self) -> ExtractionSettings:
        if not self.llm_api_key:
            self.llm_api_key = self.shared_llm_api_key
        return self

    @model_validator(mode="after")
    def _a_cloud_model_is_switched_on_twice(self) -> ExtractionSettings:
        """Refuse a cloud implementation nobody acknowledged (#392).

        Here and not in the registry, so the refusal is the settings object
        itself: nothing in module B runs on a configuration that would send
        speech out unacknowledged -- not the worker's first meeting, and not
        a route that only wanted a threshold. It names the variable to set and
        nothing else; the message carries no value from the environment.

        Settings load lazily, so by itself this refuses at B's first use.
        ``require_loadable`` is what makes it a refusal to start.
        """
        if self.llm_acknowledged_392:
            return self
        for name, value in (
            ("CLASSIFIER_IMPL", self.classifier_impl),
            ("RESOLVER_IMPL", self.resolver_impl),
            ("SUMMARY_IMPL", self.summary_impl),
        ):
            if value in CLOUD_IMPLS:
                raise ValueError(
                    f"AUTUNE_EXTRACTION_{name}={value} sends meeting text to a cloud "
                    "model. Set AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true to confirm "
                    "this deployment may: demo meetings only until #392 is decided."
                )
        return self

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


def require_loadable() -> None:
    """Raise now if module B's settings would refuse to load.

    Called when ``tasks`` is imported -- by the worker directly, by the API
    through ``router``, which imports ``tasks`` -- so a worker or an API
    given a configuration B refuses does not start, rather than starting and
    failing on the first meeting or the first request (review of #744). The
    API imports every module's router, so **a bad module B configuration
    stops the whole API**, the other modules with it -- intended: a process
    that would send speech out unacknowledged should not be half up.

    Builds the settings and throws them away. ``get_settings`` is cached,
    and priming that cache at import would pin whatever the environment held
    at that moment for every later caller.
    """
    ExtractionSettings()
