"""The agent layer's event subscriptions (agent-layer.md section 6).

Named by ``autune_core.events``' rule, so ``publish`` reaches them the way it
reaches a module's: ``autune.transcript.ready`` -> ``autune.agent.on_transcript_ready``.
``apps/worker`` includes this file by name beside the module loop (ADR 0010,
monorepo.md section 1); nothing registers a subagent's own task.

**Only the meeting id is read from the payload.** A ``TranscriptReady`` carries
the whole masked transcript; the agent reads what it needs through tools, so
the rest is left where it is and never logged.

**One periodic task wakes every subagent that declared a period**
(``Periodic``, #634). ``autune_core.periodic`` puts it on beat's schedule by its
name; it takes no arguments and finds the due subagents and teams itself.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from autune_contracts import (
    INTELLIGENCE_COMPLETED,
    INTELLIGENCE_MEETING_REPORT_CHANGED,
    TRANSCRIPT_READY,
    Payload,
    validate_major_version,
)
from autune_core import periodic, session_scope

from .main.subagents import PERIODIC_TICK
from .main.triggers import on_event, on_tick


def _meeting_id(payload: dict[str, Any]) -> str:
    envelope = Payload(
        contract_version=payload["contract_version"], meeting_id=payload["meeting_id"]
    )
    validate_major_version(envelope)
    return envelope.meeting_id


def _wake(event: str, payload: dict[str, Any], *, task_id: str | None = None) -> None:
    meeting_id = _meeting_id(payload)
    with session_scope() as session:
        on_event(event, meeting_id, session=session, task_id=task_id)


@shared_task(name="autune.agent.on_transcript_ready", acks_late=True, bind=True)
def on_transcript_ready(self: Any, payload: dict[str, Any]) -> None:
    _wake(TRANSCRIPT_READY, payload, task_id=self.request.id)
    from .live.notice import tell_participants

    with session_scope() as session:
        tell_participants(session, _meeting_id(payload))


@shared_task(name="autune.agent.on_intelligence_completed", acks_late=True, bind=True)
def on_intelligence_completed(self: Any, payload: dict[str, Any]) -> None:
    _wake(INTELLIGENCE_COMPLETED, payload, task_id=self.request.id)


@shared_task(name="autune.agent.on_intelligence_meeting_report_changed", acks_late=True, bind=True)
def on_intelligence_meeting_report_changed(self: Any, payload: dict[str, Any]) -> None:
    _wake(INTELLIGENCE_MEETING_REPORT_CHANGED, payload, task_id=self.request.id)


@shared_task(name="autune.agent.periodic.wake_subagents", acks_late=True, bind=True)
@periodic(PERIODIC_TICK)
def wake_subagents(self: Any) -> None:
    with session_scope() as session:
        on_tick(session=session, task_id=self.request.id)


@shared_task(name="autune.agent.live_detect", acks_late=True)
def live_detect(team_id: str, meeting_id: str, user_id: str, rows: list[dict[str, Any]]) -> None:
    """Live rows a browser relayed: detect questions and research each (live/service)."""
    from .live.model import GeminiLive, Row
    from .live.service import detect_and_research

    with session_scope() as session:
        detect_and_research(
            session,
            team_id=team_id,
            meeting_id=meeting_id,
            user_id=user_id,
            rows=[Row(start=float(r["start"]), text=str(r["text"])) for r in rows],
            model=GeminiLive(),
        )


@shared_task(name="autune.agent.live_research", acks_late=True)
def live_research(document_id: str, context: list[dict[str, Any]], web: bool) -> None:
    """One row a person pointed at with 조사."""
    from .live.model import GeminiLive, Row
    from .live.service import research

    with session_scope() as session:
        research(
            session,
            document_id,
            context=[Row(start=float(r["start"]), text=str(r["text"])) for r in context],
            model=GeminiLive(),
            web=web,
        )
