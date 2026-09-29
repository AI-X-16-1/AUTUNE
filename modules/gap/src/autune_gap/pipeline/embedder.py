"""Sentence embedders for ``semantic``: KURE-v1 in this process, and a fake.

``sentence_transformers`` is imported inside the class that needs it, like
``SpacyNer`` imports spaCy: the API process and the tests never load a model
stack, and the extra that provides it is optional.
"""

from __future__ import annotations

from typing import Any

from autune_core import get_logger

log = get_logger(__name__)


class FakeEmbedder:
    """No weights, no network, deterministic: a character-bigram count vector,
    not a semantic one.

    Enough for a test to build sentences the fake calls close or far without
    model weights. It is **not** an approximation of KURE-v1's judgement and
    must not be used to estimate what the real one hears — the same caution
    ``FakeNer`` states about extraction.
    """

    model_version = "fake"
    _DIM = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._DIM
        compact = "".join(text.split())
        for left, right in zip(compact, compact[1:], strict=False):
            vector[(ord(left) * 31 + ord(right)) % self._DIM] += 1.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]


class LocalKureEmbedder:
    """Weights in this process. ``nlpai-lab/KURE-v1`` by default — the model
    modules B and D already run. Not shared code (invariant 2), a shared choice.

    Loaded on first use rather than at construction, so a worker that never
    compares a meeting never pays for the weights.
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
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on the optional extra
            raise RuntimeError(
                "the local embedder needs the 'local-models' extra: "
                "uv sync --package autune-gap --extra local-models"
            ) from exc

        self._model = SentenceTransformer(self._checkpoint, device=self._device)
        log.info("gap_embedder_loaded", checkpoint=self._checkpoint, device=self._device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [[float(value) for value in vector] for vector in vectors]
