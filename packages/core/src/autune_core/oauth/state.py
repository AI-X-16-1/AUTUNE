"""OAuth state, kept only for the seconds between authorize and callback.

The ``state`` parameter defends the callback against CSRF; the ``nonce`` binds
the ID token we get back to the request we started. Both are generated in
``/google/start``, stashed here, and checked once in ``/google/callback`` — the
lookup is one-shot, a replayed callback finds nothing.

Redis (already our Celery broker) backs this in every real environment; it
expires the entry on its own if the user abandons the flow. ``InMemoryStateStore``
is the test double — same contract, no server.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Protocol

import redis

from autune_core.settings import get_settings

STATE_TTL_SECONDS = 600
_KEY_PREFIX = "oauth:state:"


class OAuthTransaction:
    """What ``/google/start`` needs to remember until the callback returns."""

    __slots__ = ("nonce", "redirect_to", "created_at")

    def __init__(self, nonce: str, redirect_to: str, created_at: float | None = None) -> None:
        self.nonce = nonce
        self.redirect_to = redirect_to
        self.created_at = created_at if created_at is not None else time.time()

    def to_json(self) -> str:
        return json.dumps(
            {"nonce": self.nonce, "redirect_to": self.redirect_to, "created_at": self.created_at}
        )

    @classmethod
    def from_json(cls, raw: str) -> OAuthTransaction:
        data = json.loads(raw)
        return cls(
            nonce=data["nonce"],
            redirect_to=data["redirect_to"],
            created_at=data.get("created_at"),
        )


class StateStore(Protocol):
    def put(self, state: str, transaction: OAuthTransaction) -> None: ...

    def pop(self, state: str) -> OAuthTransaction | None:
        """Return the transaction for ``state`` and delete it. ``None`` if unknown."""
        ...


class RedisStateStore:
    def __init__(self, client: redis.Redis, ttl_seconds: int = STATE_TTL_SECONDS) -> None:
        self._client = client
        self._ttl = ttl_seconds

    def put(self, state: str, transaction: OAuthTransaction) -> None:
        self._client.set(_KEY_PREFIX + state, transaction.to_json(), ex=self._ttl)

    def pop(self, state: str) -> OAuthTransaction | None:
        key = _KEY_PREFIX + state
        pipe = self._client.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = pipe.execute()
        if raw is None:
            return None
        return OAuthTransaction.from_json(raw.decode() if isinstance(raw, bytes) else raw)


class InMemoryStateStore:
    """Process-local store for tests. Honours the TTL so an expiry test is possible."""

    def __init__(self, ttl_seconds: int = STATE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, OAuthTransaction]] = {}

    def put(self, state: str, transaction: OAuthTransaction) -> None:
        self._entries[state] = (time.time() + self._ttl, transaction)

    def pop(self, state: str) -> OAuthTransaction | None:
        entry = self._entries.pop(state, None)
        if entry is None:
            return None
        expires_at, transaction = entry
        if time.time() > expires_at:
            return None
        return transaction


@lru_cache
def get_state_store() -> StateStore:
    return RedisStateStore(redis.from_url(get_settings().redis_url))
