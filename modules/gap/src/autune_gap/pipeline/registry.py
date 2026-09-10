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

from .base import EntityExtractor
from .ner import FakeNer, SpacyNer

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


def reset_cache() -> None:
    """Drop the cached extractor. For tests that switch implementations."""
    get_entity_extractor.cache_clear()
