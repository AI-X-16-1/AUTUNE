"""Reranker implementations. Selected by ``AUTUNE_CONTEXT_RERANKER_IMPL``.

- ``bge_reranker_v2_m3_ko_http`` — the default: dragonkue/bge-reranker-v2-m3-ko
  behind our inference server.
- ``bge_reranker_v2_m3_ko_local`` — in-process (extra: ``local-models``).
- ``fake`` — lexical-overlap heuristic, for unit tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from autune_context.pipeline._serving import probe

if TYPE_CHECKING:
    from autune_context.config import ContextSettings


class BgeRerankerKoHttp:
    """``POST {endpoint}/rerank {"query": str, "passages": [str]}`` ->
    ``{"scores": [float]}``; ``GET {endpoint}/info`` -> ``{"model_version"}``.
    """

    def __init__(self, settings: ContextSettings) -> None:
        self._client = httpx.Client(
            base_url=settings.reranker_endpoint, timeout=settings.reranker_timeout_s
        )
        info = probe(self._client, service="reranker")
        self._model_version = str(info.get("model_version", settings.reranker_local_model))

    @property
    def model_version(self) -> str:
        return self._model_version

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        resp = self._client.post("/rerank", json={"query": query, "passages": passages})
        resp.raise_for_status()
        return [float(s) for s in resp.json()["scores"]]


class BgeRerankerKoLocal:
    """In-process cross-encoder. Needs the ``local-models`` optional dependency group."""

    def __init__(self, settings: ContextSettings) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "bge_reranker_v2_m3_ko_local needs the 'local-models' extra: "
                "uv sync --package autune-context --extra local-models"
            ) from exc
        self._model = CrossEncoder(settings.reranker_local_model)
        self._model_version = settings.reranker_local_model

    @property
    def model_version(self) -> str:
        return self._model_version

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._model.predict([(query, p) for p in passages])]


class FakeReranker:
    """Jaccard token overlap between query and passage. No model, no network."""

    def __init__(self, settings: ContextSettings | None = None) -> None:
        self._model_version = "fake-reranker-v1"

    @property
    def model_version(self) -> str:
        return self._model_version

    def score(self, query: str, passages: list[str]) -> list[float]:
        q = set(query.lower().split())
        out: list[float] = []
        for passage in passages:
            p = set(passage.lower().split())
            union = q | p
            out.append(len(q & p) / len(union) if union else 0.0)
        return out
