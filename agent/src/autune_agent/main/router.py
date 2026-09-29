"""The model-shaped decisions the main agent makes, behind one seam.

The skeleton ships the interface and a fake (``autune_agent.testing``). The
Gemini implementation lands with the chat endpoint; it goes out through
``autune_integrations.privacy.check_outbound`` like B's classifier does (#393,
agent-layer.md section 8 rule 1), and it is not a LangChain chat model, so no
LangChain integration can open an outbound path of its own (section 3.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from autune_agent.results import SubagentResult


class Router(Protocol):
    def route(self, request: str, subagents: Mapping[str, str]) -> str | None:
        """Pick the subagent for ``request`` from ``{name: description}``, or
        None when none fits."""
        ...

    def compose(self, request: str, outcome: SubagentResult) -> str:
        """Turn the outcome into the chat answer."""
        ...
