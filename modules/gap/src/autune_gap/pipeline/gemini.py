"""Gemini's ``generateContent``, as this module calls it from more than one place.

The template verifier (``verifier``) and the relation assistant
(``relation_assist``) ask the same provider different questions. What they share
is here: the client, the retry and fallback, and reading the answer text back.
What each one sends is its own, and is documented where it is built.

Every request goes through ``autune_integrations.HttpClient``, so
``check_outbound`` scans every string in the body. A refusal from it
(``PrivacyViolationError``) is never caught here or by a caller: it means a
stored transcript holds an unmasked value, and the task fails (review of #484).
"""

from __future__ import annotations

import time
from typing import Any

from autune_core import get_logger
from autune_core.errors import PrivacyViolationError
from autune_integrations.errors import TransientIntegrationError

log = get_logger(__name__)

RETRY_BACKOFF_SEC = (2.0, 5.0, 10.0)
"""Module B's backoff, for the same provider and the same busy-model 503s."""


def require_scalar_addressing(value: Any, addressing: frozenset[str]) -> None:
    """Refuse a body where an ``addressing`` key holds anything but a string.

    ``check_outbound`` skips the whole value under an addressing key, so the
    exemption is safe only while those keys stay scalars. A copy of module B's
    guard, not an import of it (invariant 2); raised in review of #405.
    """
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in addressing and not isinstance(inner, str):
                raise PrivacyViolationError(
                    f"{key!r} is exempt from the outbound check only as a string"
                )
            require_scalar_addressing(inner, addressing)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            require_scalar_addressing(inner, addressing)


def http_client(service: str, base_url: str, api_key: str, timeout_sec: float) -> Any:
    """The provider as an ``autune_integrations`` client, the way every outbound
    one is written. The key travels in a header, never in the body or the URL.
    The read timeout is this client's own: a thinking model takes longer than
    the shared 10 s (module B measured 12-20 s a request)."""
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class GeminiHttpClient(HttpClient):
        addressing = frozenset({"role", "responseMimeType"})

        def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            require_scalar_addressing(kwargs.get("json"), self.addressing)
            return super().request(method, path, **kwargs)

    GeminiHttpClient.service = service
    client = GeminiHttpClient(base_url, headers={"x-goog-api-key": api_key})
    # Temporary: reaches into the shared client's private httpx instance, as
    # module B's LLM client does. If HttpClient changes how it holds it, this
    # stops applying silently and the timeout drops back to 10 s. Needs
    # HttpClient(timeout=...); see #487.
    client._client.timeout = timeout_sec  # noqa: SLF001
    return client


def answer_text(body: Any) -> str:
    """The first candidate's text, or "" -- a blocked or empty answer is not
    JSON, and the caller's parser then leaves the batch unanswered."""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    except (KeyError, IndexError, TypeError):
        return ""


class GeminiCaller:
    """One configured model, its fallback, and a count of the requests made.

    Subclasses set ``service`` (the outbound client's name, which
    ``check_outbound`` reports), ``event`` (the prefix of their log events) and
    ``key_required`` (the message a missing key raises, naming the setting that
    turned the caller on).
    """

    service = "gap-gemini"
    event = "gap_gemini"
    key_required = "a Gemini caller needs an API key"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_sec: float,
        fallback_model: str = "",
    ) -> None:
        if not api_key:
            raise ValueError(self.key_required)
        self._client = http_client(self.service, base_url, api_key, timeout_sec)
        self._model = model
        self._fallback = fallback_model
        self.requests = 0

    @property
    def model_version(self) -> str:
        """``gemini:<model>``, plus ``+<fallback>`` when one is set — either may
        have answered any batch."""
        return f"gemini:{self._model}" + (f"+{self._fallback}" if self._fallback else "")

    def _post_to(self, model: str, body: dict[str, Any], *, index: int) -> Any:
        path = f"/models/{model}:generateContent"
        for attempt, wait in enumerate(RETRY_BACKOFF_SEC, start=1):
            try:
                self.requests += 1
                return self._client.request("POST", path, json=body)
            except TransientIntegrationError as exc:
                log.info(
                    f"{self.event}_retry",
                    model=model,
                    batch=index,
                    attempt=attempt,
                    reason=str(exc),
                )
                time.sleep(wait)
        self.requests += 1
        return self._client.request("POST", path, json=body)

    def _post(self, body: dict[str, Any], *, index: int) -> Any:
        """The primary model, then the fallback if it stays unavailable. Only a
        transient failure falls back; a refused request fails the same anywhere."""
        try:
            return self._post_to(self._model, body, index=index)
        except TransientIntegrationError:
            if not self._fallback:
                raise
            log.warning(
                f"{self.event}_fallback", model=self._model, fallback=self._fallback, batch=index
            )
            return self._post_to(self._fallback, body, index=index)
