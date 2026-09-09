"""LLM implementations. Selected by ``AUTUNE_CONTEXT_LLM_IMPL``.

- ``external``         — the MVP: an OpenAI-compatible chat API. Key from
  ``autune_core`` settings (``AUTUNE_LLM_API_KEY``).
- ``self_hosted_http`` — a self-hosted OpenAI-compatible endpoint (vLLM / TGI).
- ``fake`` — echoes a canned string, for unit tests.

Not used in the MVP pipeline. First real use is Phase 2 (agenda / brief
generation). Privacy: pass only the snippet a feature needs, always masked text —
never a full transcript. See docs/architecture/privacy.md §6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from autune_core import get_settings as get_core_settings

if TYPE_CHECKING:
    from autune_context.config import ContextSettings


class _OpenAiCompatibleLlm:
    """Shared body for both hosted variants — same wire format, different base URL."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_s: float) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, headers=headers)
        self._model = model

    @property
    def model_version(self) -> str:
        return self._model

    def complete(
        self, *, system: str, user: str, max_tokens: int = 512, temperature: float = 0.0
    ) -> str:
        resp = self._client.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])


class ExternalLlm(_OpenAiCompatibleLlm):
    def __init__(self, settings: ContextSettings) -> None:
        super().__init__(
            base_url=settings.llm_api_base,
            api_key=get_core_settings().llm_api_key,
            model=settings.llm_model,
            timeout_s=settings.llm_timeout_s,
        )


class SelfHostedLlm(_OpenAiCompatibleLlm):
    def __init__(self, settings: ContextSettings) -> None:
        super().__init__(
            base_url=settings.llm_self_hosted_endpoint,
            api_key="",
            model=settings.llm_model,
            timeout_s=settings.llm_timeout_s,
        )


class FakeLlm:
    def __init__(self, settings: ContextSettings | None = None) -> None:
        self._model_version = "fake-llm-v1"

    @property
    def model_version(self) -> str:
        return self._model_version

    def complete(
        self, *, system: str, user: str, max_tokens: int = 512, temperature: float = 0.0
    ) -> str:
        return f"[fake-llm] {user[:80]}"
