"""AI pipeline for module B.

Model loading and inference only — no database writes, no HTTP beyond the shared
client. Pin model versions explicitly and load once at worker startup, not per
task.

The implementation behind each model is chosen by configuration and reached
through ``registry``; nothing outside this package names a model class.
"""

from __future__ import annotations

from .base import (
    Classifier,
    Embedder,
    NliModel,
    NliScores,
    Prediction,
    ReferenceResolver,
    ResolutionRequest,
)
from .classifier import FakeClassifier, HostedDeberta, LocalDeberta
from .embedder import FakeEmbedder, LocalKureEmbedder
from .nli import FakeNli, HostedNli, LocalNli
from .registry import get_classifier, get_embedder, get_nli, get_resolver
from .resolver import FakeResolver, HostedResolver, LocalQwenResolver

__all__ = [
    "Classifier",
    "Embedder",
    "FakeClassifier",
    "FakeEmbedder",
    "FakeNli",
    "FakeResolver",
    "HostedDeberta",
    "HostedNli",
    "HostedResolver",
    "LocalDeberta",
    "LocalKureEmbedder",
    "LocalNli",
    "LocalQwenResolver",
    "NliModel",
    "NliScores",
    "Prediction",
    "ReferenceResolver",
    "ResolutionRequest",
    "get_classifier",
    "get_embedder",
    "get_nli",
    "get_resolver",
]
