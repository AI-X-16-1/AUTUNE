"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a model class. Call ``get_classifier()``,
``get_nli()`` or ``get_resolver()``; each is cached, so the checkpoint loads on
the first call a worker makes and not on every task.
"""

from __future__ import annotations

from functools import lru_cache

from autune_extraction.config import get_settings
from autune_extraction.models import MATERIAL_EMBEDDING_DIM

from .base import Classifier, Embedder, NliModel, ReferenceResolver
from .classifier import ENSEMBLE_SEPARATOR, FakeClassifier, HostedDeberta, LocalDeberta
from .embedder import FakeEmbedder, LocalKureEmbedder
from .nli import FakeNli, HostedNli, LocalNli
from .resolver import FakeResolver, HostedResolver, LlmResolver, LocalQwenResolver
from .summary import LlmSummarizer
from .title import LlmTitler

_CLASSIFIERS: dict[str, str] = {
    "local": "weights in this process",
    "hosted": "our own inference server",
    "fake": "deterministic, for tests",
    "llm": "a cloud LLM API, masked utterance text only (pipeline.llm)",
    "llm_checked": "llm, its commitments checked by the local DeBERTa (pipeline.checked)",
}
"""Known implementations and what they are. ``llm`` and ``llm_checked`` are the
ones that leave our infrastructure -- the same requests, since ``llm_checked``
wraps ``llm`` -- opt-in and never the default; see base."""

_NLI: dict[str, str] = {
    "local": "weights in this process",
    "hosted": "our own inference server",
    "fake": "deterministic, for tests",
    "llm": "a cloud LLM API, masked ambiguous utterances only (pipeline.nli_llm)",
}
"""Same shape as ``_CLASSIFIERS``, for step 4's model (#12). ``llm`` leaves our
infrastructure -- opt-in and never the default."""

_RESOLVERS: dict[str, str] = {k: v for k, v in _CLASSIFIERS.items() if k != "llm_checked"}
"""Same names, same meaning, for the reference resolver (#175). ``llm_checked``
is a classifier arrangement with no resolver counterpart."""

_EMBEDDERS: dict[str, str] = {
    "local": "weights in this process",
    "fake": "deterministic, for tests",
}
"""No ``hosted`` yet -- see ``pipeline.embedder``."""


@lru_cache
def get_classifier() -> Classifier:
    settings = get_settings()
    impl = settings.classifier_impl

    if impl in ("local", "hosted", "llm_checked") and not settings.classifier_checkpoint:
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
    if impl == "llm":
        return _llm_classifier(impl)
    if impl == "llm_checked":
        from .checked import CheckedClassifier  # noqa: PLC0415 - same opt-in as llm

        # DeBERTa in process only: the checker exists so the check costs no
        # second outbound call, and ``hosted`` would be one to our own server
        # for the same texts.
        checker = LocalDeberta(settings.classifier_checkpoint, device=settings.classifier_device)
        return CheckedClassifier(proposer=_llm_classifier(impl), checker=checker)

    raise ValueError(
        f"unknown AUTUNE_EXTRACTION_CLASSIFIER_IMPL={impl!r}; known: {sorted(_CLASSIFIERS)}"
    )


def _llm_classifier(impl: str) -> Classifier:
    settings = get_settings()
    if not settings.llm_api_key:
        raise ValueError(
            f"AUTUNE_EXTRACTION_CLASSIFIER_IMPL={impl} needs AUTUNE_EXTRACTION_LLM_API_KEY "
            "(or the shared AUTUNE_LLM_API_KEY)"
        )
    from .llm import LlmClassifier  # noqa: PLC0415 - only a worker that opted in pays for it

    return LlmClassifier(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_sec=settings.llm_timeout_sec,
        fallback_model=settings.llm_fallback_model,
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
    if impl == "llm":
        if not settings.llm_api_key:
            raise ValueError(
                "AUTUNE_EXTRACTION_NLI_IMPL=llm needs AUTUNE_EXTRACTION_LLM_API_KEY "
                "(or the shared AUTUNE_LLM_API_KEY)"
            )
        from .nli_llm import LlmNli  # noqa: PLC0415 - only a worker that opted in pays for it

        return LlmNli(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.nli_model,
            base_url=settings.llm_base_url,
            timeout_sec=settings.llm_timeout_sec,
            fallback_model=settings.nli_fallback_model,
        )

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

    # Unset unless a threshold exists to use it with -- an embedder loaded for
    # nothing is still a model loaded, and `resolver_min_similarity` unset
    # already means "skip the similarity check" on its own.
    embedder = get_embedder() if settings.resolver_min_similarity is not None else None

    if impl == "local":
        return LocalQwenResolver(
            settings.resolver_checkpoint,
            device=settings.resolver_device,
            embedder=embedder,
            min_similarity=settings.resolver_min_similarity,
        )
    if impl == "hosted":
        if not settings.resolver_endpoint:
            raise ValueError(
                "AUTUNE_EXTRACTION_RESOLVER_IMPL=hosted needs AUTUNE_EXTRACTION_RESOLVER_ENDPOINT"
            )
        return HostedResolver(
            settings.resolver_endpoint,
            settings.resolver_checkpoint,
            embedder=embedder,
            min_similarity=settings.resolver_min_similarity,
        )
    if impl == "llm":
        if not settings.llm_api_key:
            raise ValueError(
                "AUTUNE_EXTRACTION_RESOLVER_IMPL=llm needs AUTUNE_EXTRACTION_LLM_API_KEY "
                "(or the shared AUTUNE_LLM_API_KEY)"
            )
        return LlmResolver(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.resolver_model,
            base_url=settings.llm_base_url,
            timeout_sec=settings.llm_timeout_sec,
            fallback_model=settings.resolver_second_model,
            embedder=embedder,
            min_similarity=settings.resolver_min_similarity,
        )
    if impl == "fake":
        return FakeResolver()

    raise ValueError(
        f"unknown AUTUNE_EXTRACTION_RESOLVER_IMPL={impl!r}; known: {sorted(_RESOLVERS)}"
    )


@lru_cache
def get_summarizer() -> LlmSummarizer | None:
    """The meeting summarizer (#421 v2), or ``None`` with ``summary_impl=none``."""
    settings = get_settings()
    impl = settings.summary_impl
    if impl == "none":
        return None
    if impl != "llm":
        raise ValueError(f"unknown AUTUNE_EXTRACTION_SUMMARY_IMPL={impl!r}; known: none, llm")
    if not settings.llm_api_key:
        raise ValueError(
            "AUTUNE_EXTRACTION_SUMMARY_IMPL=llm needs AUTUNE_EXTRACTION_LLM_API_KEY "
            "(or the shared AUTUNE_LLM_API_KEY)"
        )
    return LlmSummarizer(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.summary_model,
        base_url=settings.llm_base_url,
        timeout_sec=settings.llm_timeout_sec,
        fallback_model=settings.summary_fallback_model,
    )


@lru_cache
def get_titler() -> LlmTitler | None:
    """What writes a row's short title, or ``None`` with ``title_impl=none``."""
    settings = get_settings()
    impl = settings.title_impl
    if impl == "none":
        return None
    if impl != "llm":
        raise ValueError(f"unknown AUTUNE_EXTRACTION_TITLE_IMPL={impl!r}; known: none, llm")
    if not settings.llm_api_key:
        raise ValueError(
            "AUTUNE_EXTRACTION_TITLE_IMPL=llm needs AUTUNE_EXTRACTION_LLM_API_KEY "
            "(or the shared AUTUNE_LLM_API_KEY)"
        )
    return LlmTitler(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.title_model,
        base_url=settings.llm_base_url,
        timeout_sec=settings.llm_timeout_sec,
        fallback_model=settings.title_fallback_model,
    )


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    impl = settings.embedder_impl

    if impl == "local" and not settings.embedder_checkpoint:
        raise ValueError(
            "AUTUNE_EXTRACTION_EMBEDDER_IMPL=local needs AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT"
        )

    if impl == "local":
        return LocalKureEmbedder(settings.embedder_checkpoint, device=settings.embedder_device)
    if impl == "fake":
        return FakeEmbedder()

    raise ValueError(
        f"unknown AUTUNE_EXTRACTION_EMBEDDER_IMPL={impl!r}; known: {sorted(_EMBEDDERS)}"
    )


@lru_cache
def get_material_embedder() -> Embedder:
    """The embedder for uploaded materials and the questions asked of them
    (#817, ``material_search``). Same two implementations as ``get_embedder``;
    the fake is as wide as the stored column. A vector of another width is
    refused where it is used, not here: ``LocalKureEmbedder`` knows its width
    only once it has loaded."""
    settings = get_settings()
    impl = settings.material_embedder_impl
    if impl == "local":
        if not settings.embedder_checkpoint:
            raise ValueError(
                "AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL=local needs "
                "AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT"
            )
        return LocalKureEmbedder(settings.embedder_checkpoint, device=settings.embedder_device)
    if impl == "fake":
        return FakeEmbedder(dim=MATERIAL_EMBEDDING_DIM)
    raise ValueError(
        f"unknown AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL={impl!r}; known: {sorted(_EMBEDDERS)}"
    )
