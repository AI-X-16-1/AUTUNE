"""Queue module E's delivery tasks by name.

The router imports this rather than ``tasks.py``, which would pull the worker's
task graph -- B's, C's and D's event names, the Slack client -- into the API
process (gap's ``enqueue.py`` does the same). Payloads carry ids only (#275).
"""

from __future__ import annotations

from typing import Final

from celery import current_app

DELIVER_MEETING_REPORT: Final = "autune.intelligence.deliver_meeting_report"


def deliver_meeting_report(meeting_id: str, draft_id: str) -> None:
    """Post the meeting's report if it still holds ``draft_id`` when claimed."""
    current_app.send_task(DELIVER_MEETING_REPORT, args=[meeting_id, draft_id])
