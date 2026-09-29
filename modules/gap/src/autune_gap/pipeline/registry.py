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

from .base import EntityExtractor, RelationExtractor, SentenceEmbedder, TemplateVerifier
from .embedder import FakeEmbedder, LocalKureEmbedder
from .ner import FakeNer, SpacyNer
from .relations import RuleRelations
from .verifier import FakeVerifier, GeminiVerifier

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


_EMBEDDERS: dict[str, str] = {
    "off": "no sentence evidence -- keywords only, the rule-based baseline",
    "local": "KURE-v1 (or AUTUNE_GAP_EMBEDDER_CHECKPOINT) in this process",
    "fake": "deterministic, for tests",
}
"""Known sentence embedders. ``off`` is a real value, not a missing one: it is
the baseline the eval compares against, and a deployment that has not installed
the extra runs it. There is no external entry — see ``base.SentenceEmbedder``."""


@lru_cache
def get_sentence_embedder() -> SentenceEmbedder | None:
    """The configured embedder, or ``None`` when ``AUTUNE_GAP_EMBEDDER_IMPL=off``."""
    settings = get_settings()
    impl = settings.embedder_impl

    if impl == "off":
        return None
    if impl == "local":
        return LocalKureEmbedder(settings.embedder_checkpoint, device=settings.embedder_device)
    if impl == "fake":
        return FakeEmbedder()

    raise ValueError(f"unknown AUTUNE_GAP_EMBEDDER_IMPL={impl!r}; known: {sorted(_EMBEDDERS)}")


_VERIFIERS: dict[str, str] = {
    "off": "the embedding's answer stands for every utterance",
    "fake": "deterministic, for tests -- confirms the embedder's nearest candidate",
    "gemini": "EXTERNAL: ambiguous utterances go to Google's Gemini API",
}
"""Known template verifiers. ``gemini`` is the one external entry in this module,
and it is opt-in: see ``base.TemplateVerifier`` and ``verifier`` for what it
sends. Another provider is another entry here."""


@lru_cache
def get_template_verifier() -> TemplateVerifier | None:
    """The configured verifier, or ``None`` when ``AUTUNE_GAP_VERIFIER_IMPL=off``.

    A verifier checks what the embedder ranked, so one without the embedder
    would have nothing to check. That combination is refused rather than
    silently running as ``off``: somebody who set the variable expects it to
    do something."""
    settings = get_settings()
    impl = settings.verifier_impl

    if impl == "off":
        return None
    if impl not in _VERIFIERS:
        raise ValueError(f"unknown AUTUNE_GAP_VERIFIER_IMPL={impl!r}; known: {sorted(_VERIFIERS)}")
    if settings.embedder_impl == "off":
        raise ValueError(
            f"AUTUNE_GAP_VERIFIER_IMPL={impl} needs AUTUNE_GAP_EMBEDDER_IMPL=local or fake: "
            "it verifies what the embedder could not decide"
        )
    if impl == "fake":
        return FakeVerifier()
    return GeminiVerifier(
        api_key=settings.verifier_api_key,
        model=settings.verifier_model,
        base_url=settings.verifier_base_url,
        timeout_sec=settings.verifier_timeout_sec,
        fallback_model=settings.verifier_fallback_model,
    )


def reset_cache() -> None:
    """Drop the cached models. For tests and the eval, which switch implementations."""
    get_entity_extractor.cache_clear()
    get_relation_extractor.cache_clear()
    get_sentence_embedder.cache_clear()
    get_template_verifier.cache_clear()
