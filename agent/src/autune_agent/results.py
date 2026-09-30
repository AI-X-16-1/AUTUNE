"""What a tool and a subagent hand back (agent-layer.md section 4, the return contract).

A module's ``tools.py`` returns a plain dict and never imports this file -- ADR
0010's layers contract forbids a module importing ``autune_agent``. The registry
validates that dict into ``ToolResult`` when it calls the tool, so the rules below
hold for every tool whether its module enforced them or not.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ITEMS = 5
"""A tool ranks what it found and keeps five; the rest stay in the module's tables."""

_ID = re.compile(r"[a-z]+_[A-Za-z0-9]+")
"""The shape ``autune_core.ids.new_id`` produces: a prefix, an underscore, a hex tail.
Matched with ``fullmatch``: ``$`` under ``match`` would let a trailing newline through."""

_QUIET = ConfigDict(hide_input_in_errors=True)
"""A refused value is refused because it may be text -- so it stays out of the
exception message, and out of every log line that prints one (invariant 11)."""


class Finding(BaseModel):
    """One ranked item. Extra keys a module adds (``id``, ``meeting_id``) are kept."""

    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)

    title: str
    body: str = ""
    score: float = 0.0


class ToolResult(BaseModel):
    model_config = _QUIET

    ok: bool
    reason: str | None = None
    """When ``ok`` is False: why, in one line, so the agent can take another route."""
    summary: str
    """Three sentences at most. This is what the main agent reads."""
    items: list[Finding] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    """Utterance and entity ids only. Text is fetched when a step needs it."""
    confidence: float = 1.0
    truncated: bool = False
    """True when the cap cut something off. The agent must not read five as all."""

    @field_validator("evidence")
    @classmethod
    def _ids_only(cls, value: list[str]) -> list[str]:
        # An id is the one thing that keeps a transcript out of a prompt by
        # accident, so a sentence here is refused rather than passed along.
        bad = [v for v in value if not _ID.fullmatch(v)]
        if bad:
            raise ValueError(f"evidence holds ids only; got {len(bad)} non-id value(s)")
        return value

    @model_validator(mode="after")
    def _cap(self) -> ToolResult:
        if len(self.items) > MAX_ITEMS:
            self.items = self.items[:MAX_ITEMS]
            self.truncated = True
        return self

    @classmethod
    def failure(cls, reason: str, summary: str = "") -> ToolResult:
        return cls(ok=False, reason=reason, summary=summary or reason, confidence=0.0)


class ProposedAction(BaseModel):
    """Something a subagent wants done at L1 or L2 (agent-layer.md section 8).

    A subagent never calls a write tool itself. It returns these, and the main
    agent puts every L2 action through plan mode before anything happens.
    """

    model_config = _QUIET

    kind: str
    title: str
    body: str = ""
    tool: str
    """The registry name of the write the action would make, e.g. ``slack.post``."""
    arguments: dict[str, Any] = Field(default_factory=dict)
    level: Literal["L1", "L2"]
    rationale: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def _ids_only(cls, value: list[str]) -> list[str]:
        # Stored on every run, meeting or not (#449 review), so held to the
        # same rule as a tool result's evidence.
        return ToolResult._ids_only(value)


class SubagentResult(BaseModel):
    """What a subagent's graph leaves in its ``outcome`` key."""

    model_config = _QUIET

    result: ToolResult
    proposed: list[ProposedAction] = Field(default_factory=list)
