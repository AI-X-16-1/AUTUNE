"""OAuth / OpenID Connect sign-in.

``google.py`` speaks to Google's OIDC endpoints; ``state.py`` holds the
short-lived per-request state that ties an authorize redirect to its callback.
Both are plain classes with an injectable seam so the ``auth_router`` tests run
without a network or a Redis.

W2 wires Google here. Slack reuses the same shape with a different provider.
"""

from __future__ import annotations

from .google import GoogleIdentity, GoogleOAuthClient, get_google_client
from .state import (
    InMemoryStateStore,
    OAuthTransaction,
    RedisStateStore,
    StateStore,
    get_state_store,
)

__all__ = [
    "GoogleIdentity",
    "GoogleOAuthClient",
    "get_google_client",
    "InMemoryStateStore",
    "OAuthTransaction",
    "RedisStateStore",
    "StateStore",
    "get_state_store",
]
