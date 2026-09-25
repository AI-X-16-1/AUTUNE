"""The resolver's own similarity check: in-process weights, a fake. No hosted
implementation yet -- there is no inference server for anything in this module
(#113's own note), and this one is a supplementary check the resolver already
works without, so it is the last of the three worth building before something
is actually measuring it.

``sentence_transformers`` is imported inside the class that needs it, same
reason as ``LocalDeberta`` and ``LocalQwenResolver``.
"""

from __future__ import annotations

from typing import Any

from autune_core import get_logger

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
    _DIM = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._DIM
        for ch in text:
            vector[ord(ch) % self._DIM] += 1.0
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
