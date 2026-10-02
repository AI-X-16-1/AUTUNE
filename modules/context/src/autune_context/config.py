"""Module D settings. Environment prefix ``AUTUNE_CONTEXT_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.

Implementation selection lives here: ``*_impl`` picks which class backs each
model, and swapping it changes no code outside ``autune_context.pipeline``.
See docs/modules/context.md, "Model abstraction layer".
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(env_prefix="AUTUNE_CONTEXT_", env_file=".env", extra="ignore")

    # --- implementation selection (swap here, nothing else changes) ---
    embedder_impl: str = "kure_v1_http"
    reranker_impl: str = "bge_reranker_v2_m3_ko_http"
    nli_impl: str = "klue_kornli_http"
    # LLM (agenda generation) has no impl of its own yet; ``llm_impl`` below is the
    # client ``engine_mode`` "llm" / "hybrid" judge with. The pre-meeting brief is a
    # template over D's own rows and uses none.
    llm_impl: str = ""
    """Only used when ``engine_mode="llm"``: ``openai`` | ``gemini`` | ``anthropic``
    (``fake`` for tests). No default: which provider the team uses is a decision,
    and a silent default would send meeting excerpts to whichever one it named."""

    engine_mode: Literal["classic", "llm", "hybrid"] = "classic"
    """Who makes the three judgements that decide a link or a lineage.

    ``classic`` — the trained stack: re-ranker + similarity thresholds for a topic
    link, embedding cosine for which thread a decision joins, NLI + lexical cues
    for how it changed.

    ``llm`` — an external LLM makes those three judgements (``pipeline.llm_judge``).
    ``hybrid`` — ``classic``, except that a topic link the trained stack is about
    to *assert* is first put to the LLM, which can veto it (drop it, or demote it
    to ``pending``). The LLM is asked about nothing else, so it costs a fraction
    of ``llm`` mode's calls. Decision lineage runs ``classic``.

    Segmentation and candidate retrieval still run on the embedder in every mode: a
    transcript is never sent out whole (docs/architecture/privacy.md, section 6).
    Exists to compare them on the evaluation set
    (``python -m autune_context.eval --mode both``); ``classic`` stays the default
    until that comparison says otherwise."""

    # --- embedding (KURE-v1) ---
    embedding_dim: int = 1024
    """Fixed at migration time as ``ctx_embeddings.embedding vector(N)``.
    ``get_embedder()`` refuses to start if the chosen model's dimension differs."""
    embedder_endpoint: str = "http://autune-embed.internal:8080"
    embedder_timeout_s: float = 10.0
    embedder_local_model: str = "nlpai-lab/KURE-v1"
    """Only used by the ``kure_v1_local`` implementation (extra: local-models)."""

    # --- reranker (dragonkue/bge-reranker-v2-m3-ko) ---
    reranker_endpoint: str = "http://autune-rerank.internal:8080"
    reranker_timeout_s: float = 10.0
    reranker_local_model: str = "dragonkue/bge-reranker-v2-m3-ko"

    # --- nli (klue/roberta fine-tuned on KorNLI, in-house) ---
    nli_endpoint: str = "http://autune-nli.internal:8080"
    nli_timeout_s: float = 10.0
    nli_local_model: str = ""
    """Path or hub id of the in-house checkpoint. Set for ``klue_kornli_local``."""

    # --- llm (engine_mode="llm") ---
    llm_api_key: SecretStr = SecretStr("")
    """The active provider's key. Never logged: a ``SecretStr``."""
    llm_endpoint: str = ""
    """Empty means the provider's own (``LLM_DEFAULTS``). Set it for a proxy, an
    Azure OpenAI deployment or any OpenAI-compatible server."""
    llm_model: str = ""
    """Empty means the provider's default where there is one. ``openai`` and
    ``gemini`` have none: model names turn over faster than this file, so the
    operator names the model and it is recorded in every row's version column."""
    llm_effort: str = ""
    """How hard a reasoning model thinks about a verdict; empty sends nothing.
    ``anthropic``: ``output_config.effort``. ``openai``: ``reasoning_effort`` (a
    model that is not a reasoning model rejects it, hence no default). ``gemini``:
    not sent. No provider is sent a ``temperature``: the reasoning models reject
    one, so it is not part of the interface."""
    llm_max_tokens: int = 4096
    """Covers a reasoning model's thinking as well as the one-line JSON answer:
    all three providers count it against this limit, and a reply cut off by it is
    an unusable one."""
    llm_timeout_s: float = 60.0
    llm_concurrency: int = 4
    """Verdicts in flight at once for one batch of pairs."""
    llm_snippet_chars: int = 1200
    """Longest excerpt sent out, in characters, per side of a pair.
    ``check_outbound`` refuses a request whose strings total more than 4000
    (``privacy.MAX_OUTBOUND_CHARS``), prompt included; two excerpts of this size
    plus the prompt stay under it. A longer excerpt is cut, not refused."""
    llm_topic_candidates: int = 5
    """Past meetings, best hybrid-retrieval score first, the LLM is asked about
    per topic. ``classic`` re-ranks ``retrieve_top_k`` and keeps ``rerank_top_k``;
    one LLM call per candidate is what bounds this."""
    llm_thread_candidates: int = 3
    """Existing decision threads, most similar head first, the LLM is asked
    about per new decision."""
    llm_link_threshold: float = 0.5
    """Probability that a topic pair is the same topic, at or above which the
    link is asserted (below: ``pending``, unless under ``llm_pending_floor``)."""
    llm_pending_floor: float = 0.25
    """Below this the LLM is fairly sure it is a different topic, and asking the
    user would only be noise: no link row is written at all. In ``hybrid`` mode
    this is what a veto is: a link the trained stack would have asserted, dropped."""
    llm_match_threshold: float = 0.5
    """Confidence at or above which "same decision" threads a decision into an
    existing lineage."""

    @property
    def llm_model_name(self) -> str:
        """``llm_model``, or the provider's default. For reports; the client
        itself refuses to start without one."""
        return self.llm_model or LLM_DEFAULTS.get(self.llm_impl, ("", ""))[1]

    # --- topic segmentation (TextTiling) ---
    topic_window: int = 3
    """Utterances per block on each side of a candidate boundary."""
    topic_min_segment: int = 3
    """Shortest topic segment, in utterances."""
    topic_depth_threshold: float = 0.1
    """Minimum TextTiling depth score for a similarity dip to become a boundary."""

    # --- retrieval / linking knobs ---
    retrieve_top_k: int = 50
    rerank_top_k: int = 10
    rrf_k: int = 60
    link_similarity_threshold: float = 0.74
    """Dense cosine similarity between this topic's segment and a past meeting's
    closest one, at or above which the link is asserted -- the primary signal
    (see ``service._link_topic``). Set on the evaluation set's dev and first
    held-out splits (docs/modules/context.md, "Metric"): same-topic pairs
    bottomed out at 0.743, different-topic pairs reached 0.773: the two
    distributions overlap, so no value separates them all -- 0.74 keeps every
    same-topic pair and gives up three same-area ones. That three counts this
    threshold alone; the rule asserts at this similarity *or* at
    ``link_confidence_threshold``, so the asserted set can only be larger.
    Measured on short, synthetic meetings; re-check against real meetings
    before trusting it further."""
    link_confidence_threshold: float = 0.6
    """Cross-encoder score at or above which the link is also asserted,
    whatever its dense similarity. Below both thresholds: store it as
    ``pending`` and ask the user. The re-ranker scores same-topic meetings
    with different content near zero, so on its own this let almost nothing
    through; it is kept because nothing it asserted on the evaluation set was
    wrong.

    A production auto-tuning version of this (issue #256) was tried and
    reverted: confirm/reject only ever labels a ``pending`` link, which by
    definition scores *below* the current threshold, so the training sample
    can never show "the threshold is too low" evidence and a tuner fit to it
    only ever ratchets the value down. Left for #240's offline eval harness,
    which isn't subject to that bias, or a redesign that isn't."""

    # --- decision lineage ---
    lineage_match_threshold: float = 0.65
    """Cosine similarity between B's decision statement and a thread's latest
    statement, above which the decision is threaded into that existing lineage
    rather than opening a new one. 0.65 from the evaluation set's dev split,
    where 0.6 threaded two same-area but different decisions together (a
    marketing *channel* onto the marketing *budget*). Also sensitive to how
    B's ``Decision.statement`` is built — a last-utterance quote (no
    reference resolution yet, extraction issue #11) scores closing remarks
    from unrelated decisions higher than this default tolerates, so don't
    tune this threshold to today's statements."""

    # --- publishing ---
    publish_timeout_s: int = 600
    """How long topic linking waits for B before publishing ``ContextLinks`` with
    ``missing_sources=["extraction"]``. Matches E's own aggregation timeout."""

    # --- notifications ---
    max_topic_link_notices: int = 3
    """Individual topic-link Slack messages posted per meeting before the rest
    collapse into one rollup notice. A meeting with many linked topics would
    otherwise post one message per topic and flood the channel."""

    # --- pre-meeting brief ---
    brief_lead_minutes: int = 10
    """How long before a scheduled meeting's ``started_at`` its brief goes to
    the team channel. ``autune.context.periodic.send_due_briefs`` runs every
    minute, so a brief lands within a minute of this mark -- or right away for
    a meeting scheduled closer to its start than this."""

    # --- worker bootstrap ---
    warm_models_on_worker_init: bool = False
    """Set only on workers that actually consume the ``cpu_heavy`` queue.

    ``apps/worker`` imports every module's ``tasks.py`` into one Celery app
    (see docs/architecture/async-pipeline.md, "Queues"), so a warm-up hook
    registered unconditionally on ``worker_process_init`` would run in every
    worker process regardless of ``-Q`` — including ``gpu`` and ``default``
    workers that never run a context task and cannot reach the context model
    endpoints."""


LLM_DEFAULTS: dict[str, tuple[str, str]] = {
    # provider -> (endpoint, model); an empty model means the operator must name one.
    "anthropic": ("https://api.anthropic.com", "claude-opus-5-5"),
    "openai": ("https://api.openai.com", ""),
    "gemini": ("https://generativelanguage.googleapis.com", ""),
}


@lru_cache
def get_settings() -> ContextSettings:
    return ContextSettings()
