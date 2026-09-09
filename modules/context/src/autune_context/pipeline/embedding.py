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

import httpx

from autune_context.constants import EMBEDDING_DIM

if TYPE_CHECKING:
    from autune_context.config import ContextSettings


class KureHttpEmbedder:
    """KURE-v1 over HTTP. Expects ``POST {endpoint}/embed {"texts": [...]}`` ->
    ``{"embeddings": [[...]], "model_version": "..."}`` and ``GET {endpoint}/info``.
    """

    def __init__(self, settings: ContextSettings) -> None:
        self._client = httpx.Client(
            base_url=settings.embedder_endpoint, timeout=settings.embedder_timeout_s
        )
        self._model_version = self._read_version(fallback=settings.embedder_local_model)

    def _read_version(self, fallback: str) -> str:
        try:
            resp = self._client.get("/info")
            resp.raise_for_status()
            return str(resp.json()["model_version"])
        except (httpx.HTTPError, KeyError, ValueError):
            return fallback

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    @property
    def model_version(self) -> str:
        return self._model_version

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("/embed", json={"texts": texts})
        resp.raise_for_status()
        return [[float(x) for x in row] for row in resp.json()["embeddings"]]


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
        return int(self._model.get_sentence_embedding_dimension())

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
