"""Shared HTTP behaviour for the self-hosted model clients.

The three self-hosted models (embedder, reranker, NLI) are all reached the same
way: ``GET /health`` for liveness, ``GET /info`` for the pinned model id (and,
for the embedder, its output dimension). Probing at construction time means a
misconfigured or down endpoint fails the worker's ``worker_process_init``
warm-up — not a task, mid-meeting.
"""

from __future__ import annotations

from typing import Any

import httpx


def probe(client: httpx.Client, *, service: str) -> dict[str, Any]:
    """``GET /health`` then ``GET /info``. Raises ``RuntimeError`` if the endpoint
    is unreachable or unhealthy. Returns the parsed ``/info`` body."""
    try:
        client.get("/health").raise_for_status()
        info = client.get("/info")
        info.raise_for_status()
        body = info.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(
            f"{service} inference endpoint {client.base_url} is not reachable "
            f"({exc}). Check AUTUNE_CONTEXT_{service.upper()}_ENDPOINT / that the "
            "service is up, or set the impl to 'fake' for a test run."
        ) from exc
    return dict(body) if isinstance(body, dict) else {}
