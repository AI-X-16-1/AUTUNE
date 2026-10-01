"""The agent layer's event subscriptions (agent-layer.md section 6).

Named by ``autune_core.events``' rule, so ``publish`` reaches them the way it
reaches a module's: ``autune.transcript.ready`` -> ``autune.agent.on_transcript_ready``.
``apps/worker`` includes this file by name beside the module loop (ADR 0010,
monorepo.md section 1); nothing registers a subagent's own task.

**Only the meeting id is read from the payload.** A ``TranscriptReady`` carries
the whole masked transcript; the agent reads what it needs through tools, so
the rest is left where it is and never logged.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from autune_contracts import (
    INTELLIGENCE_COMPLETED,
    TRANSCRIPT_READY,
    Payload,
    validate_major_version,
)
from autune_core import session_scope

from .main.triggers import on_event


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


@shared_task(name="autune.agent.on_intelligence_completed", acks_late=True, bind=True)
def on_intelligence_completed(self: Any, payload: dict[str, Any]) -> None:
    _wake(INTELLIGENCE_COMPLETED, payload, task_id=self.request.id)
