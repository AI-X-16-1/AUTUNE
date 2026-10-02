"""The ask loop: a chat turn no subagent fits, answered from read tools.

Spec: agent/docs/specs/2026-10-02-assistant-questions-design.md. Read-only --
it proposes nothing. Every request goes through ``check_outbound``, which
counts the whole body against 4000 characters, so the loop offers a small tool
set per scope, declares each tool with one short sentence, feeds results back
compacted, and stops before a request would pass ``SIZE_LIMIT``.
"""

from __future__ import annotations

import inspect
import logging
import types
import typing
from collections.abc import Mapping
from typing import Any

from .registry import RunScope, Tool
from .toolcall import Declaration, to_wire

log = logging.getLogger(__name__)

MEETING_TOOLS = (
    "audio.meeting_overview",
    "audio.find_utterances",
    "extraction.meeting_decisions",
    "extraction.meeting_action_items",
    "extraction.unresolved_questions",
    "gap.open_gaps",
    "context.links_for_meeting",
    "intelligence.meeting_quality",
)
TEAM_TOOLS = (
    "audio.recent_meetings",
    "audio.search_team_meetings",
    "extraction.open_action_items",
    "extraction.person_action_items",
    "extraction.workload_by_owner",
    "context.list_decisions",
    "context.decision_thread",
    "intelligence.recurring_gaps",
    "intelligence.team_trend",
)
DESCRIPTION_CHARS = 120

_SCALARS = {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}


def tool_set(scope: RunScope) -> tuple[str, ...]:
    return MEETING_TOOLS if scope.meeting_id else TEAM_TOOLS


def _first_sentence(doc: str) -> str:
    flat = " ".join(doc.split())
    head = flat.split(". ", 1)[0].rstrip(".")
    return head[:DESCRIPTION_CHARS]


def _schema(annotation: Any) -> dict[str, Any] | None:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _schema(inner[0]) if len(inner) == 1 else None
    if origin is list:
        (item,) = typing.get_args(annotation) or (None,)
        return {"type": "ARRAY", "items": {"type": "STRING"}} if item is str else None
    name = _SCALARS.get(annotation)
    return {"type": name} if name else None


def declare(tools: Mapping[str, Tool], scope: RunScope) -> list[Declaration]:
    """Declarations for the scope's tool set, in its order; unregistered names skipped."""
    out: list[Declaration] = []
    for name in tool_set(scope):
        tool = tools.get(name)
        if tool is None:
            continue
        try:
            hints = typing.get_type_hints(tool.fn)
        except Exception:  # noqa: BLE001 - an unresolvable annotation drops the tool, not the turn
            log.warning("ask: %s has annotations that do not resolve", name)
            continue
        params = list(inspect.signature(tool.fn).parameters.values())[1:]
        properties: dict[str, Any] = {}
        required: list[str] = []
        usable = True
        for p in params:
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) or p.name == "team_id":
                continue
            if p.name == "meeting_id" and scope.meeting_id:
                continue
            schema = _schema(hints.get(p.name, str))
            if schema is None:
                log.warning("ask: %s has a parameter the model cannot fill", name)
                usable = False
                break
            properties[p.name] = schema
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        if not usable:
            continue
        parameters: dict[str, Any] = {"type": "OBJECT", "properties": properties}
        if required:
            parameters["required"] = required
        out.append(Declaration(to_wire(name), _first_sentence(tool.description), parameters))
    return out
