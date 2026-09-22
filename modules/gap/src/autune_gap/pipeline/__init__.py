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
)
from .ner import FakeNer, SpacyNer
from .registry import get_entity_extractor, get_relation_extractor, reset_cache
from .relations import RuleRelations

__all__ = [
    "ENTITY_LABELS",
    "RELATION_LABELS",
    "SYMMETRIC_RELATIONS",
    "Entity",
    "EntityExtractor",
    "FakeNer",
    "Relation",
    "RelationExtractor",
    "RuleRelations",
    "SpacyNer",
    "get_entity_extractor",
    "get_relation_extractor",
    "reset_cache",
]
