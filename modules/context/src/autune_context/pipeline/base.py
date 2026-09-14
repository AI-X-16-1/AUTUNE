"""Model-facing interfaces for the Meeting Context Engine.

Every AI model this module uses sits behind one of these Protocols. The concrete
implementation — self-hosted HTTP, in-process weights — is chosen by a config
string (``AUTUNE_CONTEXT_*_IMPL``) and is never referenced directly outside this
package. See docs/modules/context.md, "Model abstraction layer".

``Embedder`` / ``Reranker`` / ``NliModel`` have Phase 1 implementations.
``LlmClient`` is declared but not implemented — see its docstring.
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
        """Relevance per passage, aligned to ``passages``, in ``[0, 1]`` (the
        model's logit run through a sigmoid). Higher is more relevant."""
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
    """Text generation. Phase 2 only (agenda / brief generation) — **no Phase 1
    implementation**.

    The one path that leaves our infrastructure. It must be built on top of
    ``autune_integrations`` (or a shared LLM client added there) so
    ``check_outbound`` runs on every call — invariant 11, "privacy rules are
    code-level constraints, not policy documents". A module-local ``httpx``
    client was written and rejected in review (PR #90): a docstring saying
    "masked text only" is not the guard. Callers still pass the smallest snippet
    a feature needs, always masked.
    """

    @property
    def model_version(self) -> str: ...

    def complete(
        self, *, system: str, user: str, max_tokens: int = 512, temperature: float = 0.0
    ) -> str: ...
