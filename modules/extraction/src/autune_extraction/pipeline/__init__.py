"""AI pipeline for module B.

Model loading and inference only — no database writes, no HTTP beyond the shared
client. Pin model versions explicitly and load once at worker startup, not per
task.

The implementation behind each model is chosen by configuration and reached
through ``registry``; nothing outside this package names a model class.
"""

from __future__ import annotations

from .base import Classifier, NliModel, NliScores, Prediction
from .classifier import FakeClassifier, HostedDeberta, LocalDeberta
from .nli import FakeNli, HostedNli, LocalNli
from .registry import get_classifier, get_nli

__all__ = [
    "Classifier",
    "FakeClassifier",
    "FakeNli",
    "HostedDeberta",
    "HostedNli",
    "LocalDeberta",
    "LocalNli",
    "NliModel",
    "NliScores",
    "Prediction",
    "get_classifier",
    "get_nli",
]
