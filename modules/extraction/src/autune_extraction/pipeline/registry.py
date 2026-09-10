"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a model class. Call ``get_classifier()``;
it is cached, so the checkpoint loads on the first classification a worker does
and not on every task.
"""

from __future__ import annotations

from functools import lru_cache

from autune_extraction.config import get_settings

from .base import Classifier
from .classifier import FakeClassifier, HostedDeberta, LocalDeberta

_CLASSIFIERS: dict[str, str] = {
    "local": "weights in this process",
    "hosted": "our own inference server",
    "fake": "deterministic, for tests",
}
"""Known implementations and what they are. There is no external-API entry, and
adding one is a privacy decision rather than a dictionary key -- see base."""


@lru_cache
def get_classifier() -> Classifier:
    settings = get_settings()
    impl = settings.classifier_impl

    if impl in ("local", "hosted") and not settings.classifier_checkpoint:
        # Both record the checkpoint with every classification, and ``local``
        # loads it. Refused here, by name, rather than as a hub error from inside
        # the first forward pass -- or, for ``hosted``, as classifications stored
        # with no model version at all.
        raise ValueError(
            f"AUTUNE_EXTRACTION_CLASSIFIER_IMPL={impl} needs "
            "AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT. No trained checkpoint is "
            "published yet: train one with `python -m autune_extraction.training` "
            "and point this at its output directory, or use CLASSIFIER_IMPL=fake."
        )

    if impl == "local":
        return LocalDeberta(settings.classifier_checkpoint, device=settings.classifier_device)
    if impl == "hosted":
        if not settings.classifier_endpoint:
            raise ValueError(
                "AUTUNE_EXTRACTION_CLASSIFIER_IMPL=hosted needs "
                "AUTUNE_EXTRACTION_CLASSIFIER_ENDPOINT"
            )
        return HostedDeberta(settings.classifier_endpoint, settings.classifier_checkpoint)
    if impl == "fake":
        return FakeClassifier()

    raise ValueError(
        f"unknown AUTUNE_EXTRACTION_CLASSIFIER_IMPL={impl!r}; known: {sorted(_CLASSIFIERS)}"
    )
