"""Shared HTTP behaviour for the self-hosted model clients.

The three self-hosted models (embedder, reranker, NLI) are all reached the same
way: ``GET /health`` for liveness, ``GET /info`` for the pinned model id (and,
for the embedder, its output dimension). Probing at construction time means a
misconfigured or down endpoint fails the worker's ``worker_process_init``
warm-up — not a task, mid-meeting.

The embedder and NLI clients sit on ``ModelServerClient``, an
``autune_integrations.HttpClient``, so ``check_outbound`` reads every request
body even though the endpoint is our own: that the server is ours is exactly
the reasoning that leaves a guard unrun. The guard caps a request at
``MAX_OUTBOUND_CHARS``, so their batches are split to fit (``batches``). The
re-ranker is not on it yet: see ``reranking.BgeRerankerKoHttp``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any

import httpx

from autune_integrations.base import HttpClient
from autune_integrations.errors import IntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, assert_masked, find_pii


class ModelServerClient(HttpClient):
    """One of our inference servers, behind the outbound guard.

    Every string in a body is meeting text (masked by module A) or a decision
    statement, and nothing in it addresses the request, so ``addressing`` stays
    empty: everything is checked.
    """

    def __init__(self, base_url: str, *, service: str, timeout_s: float) -> None:
        super().__init__(base_url)
        self.service = service  # what ``check_outbound`` and the logs name
        # ``HttpClient`` fixes a 10 s timeout; each model has its own setting.
        self._client.timeout = httpx.Timeout(timeout_s, connect=5.0)

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    def health(self) -> None:
        """``GET /health``. Its body is not read, so it need not be JSON. The
        request carries nothing to check."""
        try:
            self._client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"{self.service} /health failed") from exc


def probe(client: httpx.Client | ModelServerClient, *, service: str) -> dict[str, Any]:
    """``GET /health`` then ``GET /info``. Raises ``RuntimeError`` if the endpoint
    is unreachable or unhealthy. Returns the parsed ``/info`` body."""
    try:
        if isinstance(client, ModelServerClient):
            client.health()
            body: Any = client.request("GET", "/info")
        else:
            client.get("/health").raise_for_status()
            info = client.get("/info")
            info.raise_for_status()
            body = info.json()
    except (httpx.HTTPError, IntegrationError, ValueError) as exc:
        raise RuntimeError(
            f"{service} inference endpoint {client.base_url} is not reachable "
            f"({exc}). Check AUTUNE_CONTEXT_{service.upper()}_ENDPOINT / that the "
            "service is up, or set the impl to 'fake' for a test run."
        ) from exc
    return dict(body) if isinstance(body, dict) else {}


def batches[T](
    items: Sequence[T], size: Callable[[T], int], budget: int = MAX_OUTBOUND_CHARS
) -> Iterator[list[T]]:
    """Consecutive runs of ``items`` whose ``size`` sums to at most ``budget``.

    ``check_outbound`` sums the characters of every string in a body, so
    ``size`` counts exactly those strings and no margin is guessed. An item
    over the budget on its own is the caller's to shorten first (``clip``).
    """
    batch: list[T] = []
    total = 0
    for item in items:
        n = size(item)
        if batch and total + n > budget:
            yield batch
            batch, total = [], 0
        batch.append(item)
        total += n
    if batch:
        yield batch


# How far past a cut ``clip`` looks for the end of a value cut through. Every
# shape ``find_pii`` knows is far shorter: a card number with its separators, an
# e-mail address.
CUT_MARGIN = 256


def clip(text: str, limit: int, *, destination: str) -> str:
    """``text`` cut to its first ``limit`` characters.

    The model clients cut only a text no request could carry: an utterance, or
    a decision with its source utterances, over the outbound cap. The LLM judge
    cuts each excerpt to ``llm_snippet_chars``. Cutting rather than refusing
    keeps one long line from failing a whole meeting's linking or lineage, and
    what is embedded or compared is still what was said, its opening part. The
    in-process models read it whole.

    The guard reads the request after the cut, and the opening part of a value
    cut through ("010-1234-56") matches no pattern. So the text is checked
    before it is cut, up to ``CUT_MARGIN`` characters past the cut: an unmasked
    value that starts before the cut raises ``PrivacyViolationError``
    (``assert_masked``) instead of leaving in part. The rest of the text is not
    scanned. It is not sent, and scanning cost grows faster than the text,
    which is why the guard caps what it reads."""
    if len(text) <= limit:
        return text
    window = text[: limit + CUT_MARGIN]
    if any(begin < limit for begin, _, _ in find_pii(window)):
        assert_masked(window, destination=destination)
    return text[:limit]
