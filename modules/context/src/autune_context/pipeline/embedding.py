"""Embedder implementations. Selected by ``AUTUNE_CONTEXT_EMBEDDER_IMPL``.

- ``kure_v1_http``  — the default: KURE-v1 behind our inference server.
- ``kure_v1_local`` — in-process sentence-transformers (extra: ``local-models``),
  for local development, CI-free runs, and evaluation.
- ``fake``          — deterministic, no dependencies, for unit tests.
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING

from autune_context.constants import EMBEDDING_DIM
from autune_context.pipeline._serving import ModelServerClient, batches, clip, probe
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

if TYPE_CHECKING:
    from autune_context.config import ContextSettings


class KureHttpEmbedder:
    """KURE-v1 over HTTP. ``POST {endpoint}/embed {"texts": [...]}`` ->
    ``{"embeddings": [[...]]}``; ``GET {endpoint}/info`` -> ``{"model_version",
    "dim"}`` (both read at construction, so a wrong-dimension model is caught by
    ``get_embedder()``'s guard even for the HTTP impl).

    On ``ModelServerClient``, so ``check_outbound`` reads every request: texts
    go in batches of at most ``MAX_OUTBOUND_CHARS`` characters, and a single
    text longer than that is cut to it first (``_serving.clip``).
    """

    def __init__(self, settings: ContextSettings) -> None:
        self._client = ModelServerClient(
            settings.embedder_endpoint, service="embedder", timeout_s=settings.embedder_timeout_s
        )
        info = probe(self._client, service="embedder")
        try:
            self._model_version = str(info["model_version"])
            self._dim = int(info["dim"])
        except KeyError as exc:
            raise RuntimeError(
                f"{settings.embedder_endpoint}/info is missing {exc}; "
                "get_embedder()'s dimension guard needs a real reading, not a "
                "silently-assumed default."
            ) from exc

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return self._model_version

    def embed(self, texts: list[str]) -> list[list[float]]:
        fitted = [clip(text, MAX_OUTBOUND_CHARS) for text in texts]
        vectors: list[list[float]] = []
        for batch in batches(fitted, len):
            rows = self._client.request("POST", "/embed", json={"texts": batch})["embeddings"]
            if len(rows) != len(batch):
                raise ValueError(f"asked for {len(batch)} embeddings, got {len(rows)}")
            vectors += [[float(x) for x in row] for row in rows]
        return vectors


class KureLocalEmbedder:
    """In-process KURE-v1. Needs the ``local-models`` optional dependency group."""

    def __init__(self, settings: ContextSettings) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "kure_v1_local needs the 'local-models' extra: "
                "uv sync --package autune-context --extra local-models"
            ) from exc
        self._model = SentenceTransformer(settings.embedder_local_model)
        self._model_version = settings.embedder_local_model

    @property
    def dim(self) -> int:
        reported = self._model.get_sentence_embedding_dimension()
        if reported is None:
            raise RuntimeError(f"{self._model_version} did not report an embedding dimension")
        return int(reported)

    @property
    def model_version(self) -> str:
        return self._model_version

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.encode(texts, normalize_embeddings=True)]


class FakeEmbedder:
    """Deterministic unit-normalised vectors derived from a hash of the text.

    Same text -> same vector, so retrieval and idempotency tests are stable
    without a model or a network.
    """

    def __init__(self, settings: ContextSettings | None = None) -> None:
        self._model_version = "fake-embedder-v1"

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    @property
    def model_version(self) -> str:
        return self._model_version

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        raw = hashlib.sha256(text.encode("utf-8")).digest()
        vals = [((raw[i % len(raw)] / 255.0) * 2.0 - 1.0) for i in range(EMBEDDING_DIM)]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]
