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

    medium_threshold: float = 0.5
    """Down to here is ``medium``; below it ``low``. #35 puts the band at
    0.5–0.7 and says the numbers live here rather than in the code."""

    default_template: str = "general"
    """Which domain template applies to a meeting nobody chose one for.

    Always applied rather than inferred: a template picked from the title or
    from the topics would, when wrong, make every one of its items a false gap,
    and the metric is precision (#22). A meeting overrides it explicitly through
    ``PUT /api/gap/templates/{meeting_id}``, stored in ``gap_meeting_template``."""

    partial_centrality: float = 0.4
    """A matched topic below this carried too little of the meeting to count the
    item as settled, so the gap is raised as ``partial`` rather than dropped.
    Centrality is PageRank normalised so the meeting's top topic is 1."""

    partial_damping: float = 0.7
    """What a ``partial`` finding's score is multiplied by. "Named but thin" is
    a weaker claim than "never came up", and a false gap costs more than a
    missed one."""

    weight_template: float = 0.4
    weight_coverage: float = 0.4
    weight_participation: float = 0.2
    """The three risk inputs (#35): how much the item's absence matters, how
    thinly the meeting covered it, and how much of the room was silent on it.

    Relative, not absolute — ``detect.score`` renormalises over the signals it
    could actually measure, so a missing item scores on the first two alone
    instead of being charged a zero for participation it has no topic to read."""

    ner_impl: str = "spacy"
    """Which entity extractor to run: ``spacy`` or ``fake``.

    No ``external``. Extraction runs over every utterance of a meeting, so
    sending it to somebody else's model is a decision about where personal data
    goes rather than a config value — see ``pipeline.base``."""

    relation_impl: str = "rule"
    """Which relation extractor to run: ``rule`` or ``gemini``.

    ``rule`` is the marker rules alone, in this process. **``gemini`` runs the
    same rules and then sends the utterances holding a pair the rules decline
    to read — 는데/지만 glue, a bare 의, a reason read as resolved — to Google**,
    each masked but with any name or spoken-out number in it, and never the rest
    of the meeting. Unlike ``ner_impl`` this may have an external entry at all
    because a relation needs the clause, not the transcript. It is still
    privacy.md section 6's design conversation, so it is never the default and
    belongs on dummy meetings until the team decides otherwise, as with
    ``verifier_impl``. It uses the verifier's provider settings — key, model,
    fallback, base URL and timeout. See ``pipeline.relation_assist``."""

    relation_assist_max_utterances: int = 30
    """At most this many utterances of one meeting are sent by ``gemini``
    relation assistance in a run, in meeting order. The rest keep the rules'
    answer. A bound on how much of a meeting can leave, not a tuning knob."""

    ner_model: str = "ko_core_news_lg"
    """The pipeline to load. A **name**, not a version.

    The version is read from the loaded pipeline's own ``meta`` and recorded on
    every row as ``gap_topics.extractor_version`` — this string alone could not
    tell a graph built with 3.7 from one built with 3.8. The wheel is pinned in
    the ``local-models`` extra, so which version loads is decided by
    ``uv.lock`` rather than by the day somebody ran ``spacy download``.

    A general Korean pipeline trained on written text; meeting speech is spoken
    Korean, so recall is expected to be poor until #13 fine-tunes one."""

    embedder_impl: str = "off"
    """Which sentence embedder reads the speech for template comparison:
    ``off``, ``local`` or ``fake``.

    ``off`` is the rule-based baseline — an item counts as said only when one of
    its keywords was. ``local`` adds KURE-v1 in this process, which hears an item
    settled with a verb or a date and no noun (``autune_gap.semantic``).

    **Off by default until real meetings say otherwise.** The eval set is four
    authored meetings, and the two numbers below were chosen by looking at
    them; ``python -m autune_gap.eval --compare`` prints both runs side by side
    so the W5 meetings can decide. No ``external``: the input is every consenting
    utterance of the meeting (``pipeline.base.SentenceEmbedder``)."""

    embedder_checkpoint: str = "nlpai-lab/KURE-v1"
    """The model ``local`` loads. Module D's shipped choice, and module B's."""

    embedder_device: str = "cpu"
    """``cpu`` or ``cuda``. Never inferred from the machine, for the reason
    module B gives: a latency measured on one scheduling says nothing about the
    other."""

    semantic_floor: float = 0.55
    """The cosine an utterance needs with an item's closest example sentence to
    count as having said the item. Below it the utterance is about nothing on
    the checklist, whatever it was nearest to."""

    semantic_margin: float = 0.0
    """How far the winning item must lead the runner-up — another item or the
    background class. ``0`` means the nearest one wins outright; raising it
    makes an utterance close to two items count for neither."""

    verifier_impl: str = "off"
    """Which verifier checks the utterances the embedder is unsure of: ``off``,
    ``fake`` or ``gemini``. Needs the embedder on (``embedder_impl``).

    ``off`` keeps the embedding's own answer for every utterance. **``gemini``
    sends each ambiguous utterance, masked but with any name or spoken-out number in it, to
    Google** along with its candidate items — never the rest of the meeting.
    That is privacy.md section 6's design conversation, so it is never the
    default and belongs on dummy meetings until the team decides otherwise
    (module B's ``llm`` classifier is the same case, #392). See
    ``pipeline.verifier`` for exactly what a request carries."""

    verifier_model: str = "gemini-3.8-flash"
    """The model ``gemini`` calls. Module B's default."""

    verifier_fallback_model: str = "gemini-3.5-flash-lite"
    """Answers a batch when ``verifier_model`` stays unavailable (429, 5xx,
    timeout after retries). Blank disables it."""

    verifier_api_key: str = ""
    """Provider key for ``gemini``, sent as a header only. Blank by default, and
    ``gemini`` refuses to start without one. A free-tier key may let the
    provider keep what it is sent."""

    verifier_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    """The provider's API root."""

    verifier_timeout_sec: float = 60.0
    """Per request, connect and read. Module B measured 12-20 s a request for a
    thinking model, past the shared client's 10 s."""

    verify_confident_score: float = 0.6
    """An item that wins with at least this cosine, and ``verify_confident_lead``
    ahead of the runner-up, is taken without asking the verifier."""

    verify_confident_lead: float = 0.05
    """The lead a winner needs to be taken without asking — an item's to be
    heard, the background class's to be dismissed."""

    verify_candidate_score: float = 0.45
    """An item below this cosine is not offered to the verifier, and an
    utterance with no item at or above it is not asked about at all."""

    verify_candidates: int = 3
    """How many items, at most, one utterance is checked against — the
    embedder's nearest. The verifier cannot answer outside them."""

    verify_examples: int = 2
    """Example sentences sent per candidate item. Template content, not meeting
    content, but it counts against the outbound size cap."""

    verify_max_utterances: int = 30
    """At most this many utterances of one meeting are sent in a run. The rest
    of the ambiguous ones keep the embedding's own answer. A bound on how much of
    a meeting can leave, not a tuning knob."""

    rescore_max_attempts: int = 5
    """How many times in a row the periodic rescore tries one meeting at one
    grouping of people before leaving it until the grouping moves again (#516).
    Five tries is fifty minutes at the ten-minute sweep: enough for a provider
    outage to pass, and a bound on the quota a meeting that always fails can
    spend."""


@lru_cache
def get_settings() -> GapSettings:
    return GapSettings()
