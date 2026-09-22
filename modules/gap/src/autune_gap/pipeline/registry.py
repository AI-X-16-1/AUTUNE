"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a model class. Call
``get_entity_extractor()``; it is cached, so the pipeline loads on the first
extraction a worker does and not on every task.

**There is no ``worker_process_init`` warm-up hook here, on purpose.**
``apps/worker`` imports every module's ``tasks.py`` into one Celery app, so a
hook registered here would run in every worker process regardless of ``-Q`` —
including workers that never run a gap task. Module D shipped one and had to
gate it behind a setting (PR #90); the cost of not having one is that the first
task of a process pays the load, which for a spaCy pipeline is seconds, not
minutes.
"""

from __future__ import annotations

from functools import lru_cache

from autune_gap.config import get_settings

from .base import EntityExtractor, RelationExtractor
from .ner import FakeNer, SpacyNer
from .relations import RuleRelations

_EXTRACTORS: dict[str, str] = {
    "spacy": "a Korean spaCy pipeline in this process",
    "fake": "deterministic, for tests",
}
"""Known implementations and what they are. There is no external-API entry, and
adding one is a privacy decision rather than a dictionary key — see ``base``."""


@lru_cache
def get_entity_extractor() -> EntityExtractor:
    settings = get_settings()
    impl = settings.ner_impl

    if impl == "spacy":
        return SpacyNer(settings.ner_model)
    if impl == "fake":
        return FakeNer()

    raise ValueError(f"unknown AUTUNE_GAP_NER_IMPL={impl!r}; known: {sorted(_EXTRACTORS)}")


_RELATION_EXTRACTORS: dict[str, str] = {
    "rule": "marker rules over the entities already found, in this process",
}
"""Known implementations of step 2. One, and the registry exists anyway.

This is the step that is promised LLM assistance for its hard cases
(``docs/modules/gap.md``, issue #32), so the seam is what a second entry plugs
into. Unlike entity extraction an assisted implementation here is *allowed* to
exist — a relation needs a clause, not a transcript — but it goes through
``autune_integrations`` rather than a client of its own. See ``base``.
"""


@lru_cache
def get_relation_extractor() -> RelationExtractor:
    settings = get_settings()
    impl = settings.relation_impl

    if impl == "rule":
        return RuleRelations()

    raise ValueError(
        f"unknown AUTUNE_GAP_RELATION_IMPL={impl!r}; known: {sorted(_RELATION_EXTRACTORS)}"
    )


def reset_cache() -> None:
    """Drop the cached extractors. For tests that switch implementations."""
    get_entity_extractor.cache_clear()
    get_relation_extractor.cache_clear()
