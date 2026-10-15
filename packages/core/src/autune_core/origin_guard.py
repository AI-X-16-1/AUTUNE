"""Refuse a browser request whose ``Origin`` is another site's.

The session cookie is ``SameSite=Lax``. That keeps it off a cross-*site*
``fetch``, but it does not separate two names of one site: ``autune.cloud``
and ``app.autune.cloud`` are the same site, and a form on the first posts to
the second with the cookie attached. A browser says where such a request
comes from in ``Origin``, on every ``POST`` and on a WebSocket handshake, and
this refuses the ones that name a different host than the one that was asked.

What it does and does not do:

- Only requests that change state are looked at (anything but ``GET``,
  ``HEAD`` and ``OPTIONS``), and WebSocket handshakes, which are ``GET``
  requests that open a live channel with the session cookie.
- A request **without** an ``Origin`` is let through. Slack's servers post to
  ``/api/slack/events`` with none, and are authenticated by a signature, not
  a cookie; so are other non-browser clients. A browser always sends it on the
  requests above.
- The comparison is of host names, not of the full origin: TLS ends at the
  load balancer and nginx hands the API ``Host`` without a port, so scheme and
  port are not known here and are not what separates ``autune.cloud`` from
  ``app.autune.cloud``.
- It is off where cookies are not Secure (local development), where the web
  app proxies through another port and ``Host`` is not the browser's.

``allowed_origins`` are extra origins that may call (``cors_allowed_origins``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from .errors import PermissionDeniedError
from .logging import get_logger

log = get_logger(__name__)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_of(value: str) -> str:
    """The host name of an origin or a ``Host`` header, lower case, no port."""
    value = value.strip().lower()
    if "://" not in value:
        value = "//" + value
    return urlsplit(value).hostname or ""


class OriginGuard:
    """ASGI middleware: see the module docstring."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        enforce: bool,
        allowed_origins: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.enforce = enforce
        self.allowed_hosts = frozenset(h for h in (_host_of(o) for o in allowed_origins) if h)

    def _refused(self, scope: Scope) -> bool:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin")
        if origin is None:
            return False
        host = _host_of(origin)
        if not host:  # "null", a sandboxed frame or a file: page
            return True
        return host != _host_of(headers.get("host", "")) and host not in self.allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if not self.enforce or kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if kind == "http" and scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        if not self._refused(scope):
            await self.app(scope, receive, send)
            return

        log.warning("origin_refused", kind=kind, path=scope.get("path"))
        if kind == "websocket":
            # Closing before accept makes the handshake a 403.
            await send({"type": "websocket.close", "code": 1008})
            return
        error = PermissionDeniedError("this request comes from another site")
        await JSONResponse(error.to_dict(), status_code=error.status_code)(scope, receive, send)
