"""Embedders: in-process weights, our own inference server, a fake.

The resolver's similarity check (``get_embedder``) uses the in-process and fake
ones only -- it is a supplementary check the resolver already works without.
Uploaded materials (``get_material_embedder``, #817) can also use
``HostedKureEmbedder``: the same model on our own inference server, which is
where ``privacy.md`` says a material's vectors are made.

``sentence_transformers`` is imported inside the class that needs it, same
reason as ``LocalDeberta`` and ``LocalQwenResolver``.
"""

from __future__ import annotations

import time
from typing import Any

from autune_core import get_logger
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .classifier import RETRY_BACKOFF_SEC, _batches_within_budget

log = get_logger(__name__)


class FakeEmbedder:
    """No weights, no network, deterministic: a crude character-overlap vector,
    not a semantic one.

    Enough for a test to construct a request the fake calls "similar" or
    "dissimilar" without depending on real model weights -- not an
    approximation of KURE-v1's actual judgment, the same caution
    ``FakeClassifier`` states about accuracy.
    """

    model_version = "fake"

    def __init__(self, dim: int = 64) -> None:
        # 64 for the resolver's check, which compares two vectors it made
        # itself; a material's vectors are stored in a fixed-width column and
        # the fake has to fill it (``models.MATERIAL_EMBEDDING_DIM``).
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for ch in text:
            vector[ord(ch) % self._dim] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]


class LocalKureEmbedder:
    """Weights in this process. ``nlpai-lab/KURE-v1``, the same model module D
    already runs in production for retrieval -- not shared code (invariant 2),
    but a shared choice: it is Korean-tuned and already the team's answer to
    "does this sentence mean the same thing as that one".
    """

    def __init__(self, checkpoint: str, *, device: str = "cpu") -> None:
        if not checkpoint:
            raise ValueError("LocalKureEmbedder needs a checkpoint")
        self._checkpoint = checkpoint
        self._device = device
        self._model: Any = None

    @property
    def model_version(self) -> str:
        return self._checkpoint

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "the local embedder needs the 'local-models' extra: "
                "uv sync --package autune-extraction --extra local-models"
            ) from exc

        self._model = SentenceTransformer(self._checkpoint, device=self._device)
        log.info("extraction_embedder_loaded", checkpoint=self._checkpoint, device=self._device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(vector) for vector in vectors]


def _embedder_client(endpoint: str) -> Any:
    """Our inference server, as a client the way every other one is written --
    see ``classifier._classifier_client``. A subclass rather than a reassigned
    instance for the same reason: ``addressing`` can only be declared on a
    class, and every string here is a masked piece of a material or a masked
    question -- nothing addresses the request, so declaring nothing is what
    fails closed.
    """
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class EmbedderClient(HttpClient):
        service = "extraction-embedder"

    return EmbedderClient(endpoint)


class HostedKureEmbedder:
    """The same model as ``LocalKureEmbedder``, on our own inference server.

    Expects ``POST {endpoint}/embed {"texts": [...]}`` ->
    ``{"embeddings": [[...], ...]}``, the route the self-hosted serving
    contract names (``docs/modules/context.md``) and module D's own hosted
    client calls -- a real server behind this answers both modules the same
    way, even though each module's client is its own copy (invariant 2).

    **Only masked text is given to it.** Its callers mask first: an upload's
    pieces are cut from the masked document (``materials.store_upload``) and a
    question is masked before it is embedded (``material_search``). It goes
    through ``autune_integrations.HttpClient``, so the outbound guard runs on
    every request body as well. Nothing here logs a text; a log line or an
    error carries counts and widths only.

    **Every vector must be ``dim`` wide.** A server answering another count or
    width is refused here with ``ValueError``: a vector of the wrong width
    cannot be stored in ``ext_material_chunks.embedding``, and it says the
    server runs a model other than the one recorded as ``model_version``.

    Requests are split to fit the outbound guard and each one is re-attempted
    on a transient failure, the way ``HostedDeberta`` does it -- see its
    ``_post``. A 4xx surfaces as ``PermanentIntegrationError``, an outage that
    outlasts the re-attempts as ``TransientIntegrationError``.
    """

    def __init__(self, endpoint: str, model_version: str, *, dim: int) -> None:
        self._client = _embedder_client(endpoint)
        self._model_version = model_version
        self._dim = dim

    @property
    def model_version(self) -> str:
        return self._model_version

    def _post(self, batch: list[str], *, index: int) -> Any:
        """One batch, re-attempted while the failure is a transient one --
        see ``HostedDeberta._post``. ``/embed`` stores nothing, so repeating
        it is safe."""
        for attempt, wait in enumerate(RETRY_BACKOFF_SEC, start=1):
            try:
                return self._client.request("POST", "/embed", json={"texts": batch})
            except TransientIntegrationError as exc:
                # Counts and a reason only. Every string in the batch is
                # material text or a question, so none of it is loggable.
                log.info(
                    "extraction_embedder_retry",
                    batch=index,
                    texts=len(batch),
                    attempt=attempt,
                    reason=str(exc),
                )
                time.sleep(wait)
        return self._client.request("POST", "/embed", json={"texts": batch})

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for index, batch in enumerate(_batches_within_budget(texts, MAX_OUTBOUND_CHARS)):
            body = self._post(batch, index=index)
            rows = body.get("embeddings", []) if isinstance(body, dict) else body
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ValueError(
                    f"asked for {len(batch)} embeddings, got {type(rows).__name__} "
                    f"of length {len(rows) if isinstance(rows, list) else 'n/a'}"
                )
            for row in rows:
                if not isinstance(row, list) or len(row) != self._dim:
                    width = len(row) if isinstance(row, list) else "n/a"
                    raise ValueError(
                        f"the inference server answered a vector of width {width}, "
                        f"not {self._dim}; check that "
                        "AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_ENDPOINT serves the model "
                        "AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT names"
                    )
                vectors.append([float(x) for x in row])
        return vectors
