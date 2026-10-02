"""Model-facing interfaces for the Meeting Context Engine.

Every AI model this module uses sits behind one of these Protocols. The concrete
implementation — self-hosted HTTP, in-process weights — is chosen by a config
string (``AUTUNE_CONTEXT_*_IMPL``) and is never referenced directly outside this
package. See docs/modules/context.md, "Model abstraction layer".

``Embedder`` / ``Reranker`` / ``NliModel`` are the trained stack (``engine_mode
="classic"``). ``LlmClient`` backs the LLM alternative (``engine_mode="llm"``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
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


@dataclass
class LlmUsage:
    """What an ``LlmClient`` has spent so far in this process.

    The evaluation harness reads it to put a price and a latency next to the
    accuracy of ``engine_mode="llm"``. ``unjudged`` is incremented by
    ``LlmJudge``, not by the client: a call that came back unusable (refused,
    truncated, not JSON) or that the outbound guard refused.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    unjudged: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False, init=False
    )
    """A batch of pairs is asked concurrently, and ``+=`` on an attribute is not
    atomic."""

    def record_call(self, *, input_tokens: int, output_tokens: int, seconds: float) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.seconds += seconds

    def record_unjudged(self) -> None:
        with self._lock:
            self.unjudged += 1


@runtime_checkable
class LlmClient(Protocol):
    """Text generation through an external API. Used by ``engine_mode="llm"``
    (the LLM-backed alternative to the trained stack, see ``pipeline.llm_judge``);
    agenda / brief generation (Phase 2) would use the same client.

    The one path that leaves our infrastructure. It is built on top of
    ``autune_integrations.HttpClient`` so ``check_outbound`` runs on every call —
    invariant 11, "privacy rules are code-level constraints, not policy
    documents". A module-local ``httpx`` client was written and rejected in
    review (PR #90): a docstring saying "masked text only" is not the guard.
    Callers still pass the smallest snippet a feature needs, always masked.

    No ``temperature``: the current models reject non-default sampling
    parameters, so an interface that accepts one would promise a determinism it
    cannot deliver.
    """

    @property
    def model_version(self) -> str: ...

    @property
    def usage(self) -> LlmUsage: ...

    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str: ...
