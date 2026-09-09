"""Model-facing interfaces for module D.

Every AI model this module uses sits behind one of these Protocols. The concrete
implementation — self-hosted HTTP, in-process weights, or external API — is
chosen by a config string (``AUTUNE_CONTEXT_*_IMPL``) and is never referenced
directly outside this package. See docs/modules/context.md, "Model abstraction
layer".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NliScores:
    """One premise/hypothesis result. ``label`` is the argmax of the three."""

    label: str  # "entailment" | "contradiction" | "neutral"
    entailment: float
    contradiction: float
    neutral: float


@runtime_checkable
class Embedder(Protocol):
    """Dense text embedding. KURE-v1 by default."""

    @property
    def dim(self) -> int: ...

    @property
    def model_version(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input text, each of length ``dim``."""
        ...


@runtime_checkable
class Reranker(Protocol):
    """Query/passage relevance scoring. dragonkue/bge-reranker-v2-m3-ko by default."""

    @property
    def model_version(self) -> str: ...

    def score(self, query: str, passages: list[str]) -> list[float]:
        """One relevance score per passage, aligned to ``passages``. Higher is better."""
        ...


@runtime_checkable
class NliModel(Protocol):
    """Premise/hypothesis entailment. klue/roberta fine-tuned on KorNLI by default."""

    @property
    def model_version(self) -> str: ...

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        """One result per ``(premise, hypothesis)`` pair, aligned to ``pairs``."""
        ...


@runtime_checkable
class LlmClient(Protocol):
    """Text generation. External API for the MVP, self-hosted later.

    Privacy: callers pass the smallest snippet a feature needs, never a full
    transcript, and always masked text. See docs/architecture/privacy.md §6.
    """

    @property
    def model_version(self) -> str: ...

    def complete(
        self, *, system: str, user: str, max_tokens: int = 512, temperature: float = 0.0
    ) -> str: ...
