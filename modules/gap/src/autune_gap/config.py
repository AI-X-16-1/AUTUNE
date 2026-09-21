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

    ner_model: str = "ko_core_news_lg"
    """The pipeline to load. A **name**, not a version.

    The version is read from the loaded pipeline's own ``meta`` and recorded on
    every row as ``gap_topics.extractor_version`` — this string alone could not
    tell a graph built with 3.7 from one built with 3.8. The wheel is pinned in
    the ``local-models`` extra, so which version loads is decided by
    ``uv.lock`` rather than by the day somebody ran ``spacy download``.

    A general Korean pipeline trained on written text; meeting speech is spoken
    Korean, so recall is expected to be poor until #13 fine-tunes one."""


@lru_cache
def get_settings() -> GapSettings:
    return GapSettings()
