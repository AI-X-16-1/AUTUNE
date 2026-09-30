"""AI pipeline for module C.

Model loading and inference only — no database writes, no HTTP. Pin model
versions explicitly and load once per worker process, not per task.

The implementation behind each model is chosen by configuration and reached
through ``registry``; nothing outside this package names a model class.
"""

from __future__ import annotations

from .base import (
    ENTITY_LABELS,
    RELATION_LABELS,
    SYMMETRIC_RELATIONS,
    Entity,
    EntityExtractor,
    Relation,
    RelationExtractor,
    SentenceEmbedder,
    TemplateVerifier,
)
from .embedder import FakeEmbedder, LocalKureEmbedder
from .ner import FakeNer, SpacyNer
from .registry import (
    get_entity_extractor,
    get_relation_extractor,
    get_sentence_embedder,
    get_template_verifier,
    reset_cache,
)
from .relation_assist import AssistedRelations, FakeRelationAsker, GeminiRelationAsker
from .relations import RuleRelations
from .verifier import FakeVerifier, GeminiVerifier

__all__ = [
    "ENTITY_LABELS",
    "RELATION_LABELS",
    "SYMMETRIC_RELATIONS",
    "AssistedRelations",
    "Entity",
    "EntityExtractor",
    "FakeEmbedder",
    "FakeNer",
    "FakeRelationAsker",
    "FakeVerifier",
    "GeminiRelationAsker",
    "GeminiVerifier",
    "LocalKureEmbedder",
    "Relation",
    "RelationExtractor",
    "RuleRelations",
    "SentenceEmbedder",
    "SpacyNer",
    "TemplateVerifier",
    "get_entity_extractor",
    "get_relation_extractor",
    "get_sentence_embedder",
    "get_template_verifier",
    "reset_cache",
]
