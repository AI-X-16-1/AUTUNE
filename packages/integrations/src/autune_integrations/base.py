"""Shared client behavior: one place for timeouts, retries and error mapping.

Five modules talk to Slack. Without this, they get five different retry
policies and five different ways of failing.
"""

from __future__ import annotations

from typing import Any

import httpx

from autune_core import get_logger

from .errors import PermanentIntegrationError, TransientIntegrationError
from .privacy import check_outbound

log = get_logger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class HttpClient:
    """Thin wrapper over httpx with the error split every caller needs.

    Subclasses supply the base URL and auth headers. Nothing here knows about a
    specific product — that belongs in the per-service client.
    """

    service: str = "http"

    addressing: frozenset[str] = frozenset()
    """Keys whose values address the request rather than carry meeting content.

    Everything else in the body is checked. Declaring nothing means everything
    is checked, so forgetting to declare fails closed."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url, headers=headers or {}, timeout=DEFAULT_TIMEOUT
        )

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        # Here rather than in each method: a per-method call is a step someone
        # forgets when they add the next endpoint, and forgetting it is silent.
        body = kwargs.get("json")
        if body is not None:
            check_outbound(body, destination=self.service, addressing=self.addressing)

        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TransientIntegrationError(f"{self.service} timed out") from exc
        except httpx.TransportError as exc:
            raise TransientIntegrationError(f"{self.service} is unreachable") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise TransientIntegrationError(f"{self.service} returned {response.status_code}")
        if response.status_code >= 400:
            # The body may echo request content, so it is not put in the message.
            raise PermanentIntegrationError(
                f"{self.service} rejected the request with {response.status_code}"
            )

        log.info("integration_call", service=self.service, method=method, path=path)
        return response.json() if response.content else {}

    def close(self) -> None:
        self._client.close()
