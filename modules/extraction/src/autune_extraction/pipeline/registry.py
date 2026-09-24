"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a model class. Call ``get_classifier()``,
``get_nli()`` or ``get_resolver()``; each is cached, so the checkpoint loads on
the first call a worker makes and not on every task.
"""

from __future__ import annotations

from functools import lru_cache

from autune_extraction.config import get_settings

from .base import Classifier, NliModel, ReferenceResolver
from .classifier import ENSEMBLE_SEPARATOR, FakeClassifier, HostedDeberta, LocalDeberta
from .nli import FakeNli, HostedNli, LocalNli
from .resolver import FakeResolver, HostedResolver, LocalQwenResolver

_CLASSIFIERS: dict[str, str] = {
    "local": "weights in this process",
    "hosted": "our own inference server",
    "fake": "deterministic, for tests",
}
"""Known implementations and what they are. There is no external-API entry, and
adding one is a privacy decision rather than a dictionary key -- see base."""

_NLI: dict[str, str] = {
    "local": "weights in this process",
    "hosted": "our own inference server",
    "fake": "deterministic, for tests",
}
"""Same shape as ``_CLASSIFIERS``, for step 4's model (#12)."""

_RESOLVERS: dict[str, str] = dict(_CLASSIFIERS)
"""Same three names, same meaning, for the reference resolver (#175)."""


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
        if ENSEMBLE_SEPARATOR in settings.classifier_checkpoint:
            # The inference server runs one model and is told its version; a list
            # here would be recorded as the version of a model that never ran.
            raise ValueError(
                "AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT lists several checkpoints, "
                "which only CLASSIFIER_IMPL=local can ensemble"
            )
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


@lru_cache
def get_nli() -> NliModel:
    settings = get_settings()
    impl = settings.nli_impl

    if impl in ("local", "hosted") and not settings.nli_checkpoint:
        # Same reasoning as get_classifier(): named here rather than surfacing
        # as a hub 404 from inside the first premise/hypothesis call.
        raise ValueError(
            f"AUTUNE_EXTRACTION_NLI_IMPL={impl} needs AUTUNE_EXTRACTION_NLI_CHECKPOINT. "
            "#172 settled on klue/roberta-base fine-tuned on KorNLI -- point this at "
            "that checkpoint, or use NLI_IMPL=fake."
        )

    if impl == "local":
        return LocalNli(settings.nli_checkpoint, device=settings.nli_device)
    if impl == "hosted":
        if not settings.nli_endpoint:
            raise ValueError(
                "AUTUNE_EXTRACTION_NLI_IMPL=hosted needs AUTUNE_EXTRACTION_NLI_ENDPOINT"
            )
        return HostedNli(settings.nli_endpoint, settings.nli_checkpoint)
    if impl == "fake":
        return FakeNli()

    raise ValueError(f"unknown AUTUNE_EXTRACTION_NLI_IMPL={impl!r}; known: {sorted(_NLI)}")


@lru_cache
def get_resolver() -> ReferenceResolver:
    settings = get_settings()
    impl = settings.resolver_impl

    if impl in ("local", "hosted") and not settings.resolver_checkpoint:
        raise ValueError(
            f"AUTUNE_EXTRACTION_RESOLVER_IMPL={impl} needs "
            "AUTUNE_EXTRACTION_RESOLVER_CHECKPOINT. Use RESOLVER_IMPL=fake until #175's "
            "model choice is confirmed."
        )

    if impl == "local":
        return LocalQwenResolver(settings.resolver_checkpoint, device=settings.resolver_device)
    if impl == "hosted":
        if not settings.resolver_endpoint:
            raise ValueError(
                "AUTUNE_EXTRACTION_RESOLVER_IMPL=hosted needs AUTUNE_EXTRACTION_RESOLVER_ENDPOINT"
            )
        return HostedResolver(settings.resolver_endpoint, settings.resolver_checkpoint)
    if impl == "fake":
        return FakeResolver()

    raise ValueError(
        f"unknown AUTUNE_EXTRACTION_RESOLVER_IMPL={impl!r}; known: {sorted(_RESOLVERS)}"
    )
