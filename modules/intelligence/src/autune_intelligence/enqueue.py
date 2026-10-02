"""Queue module E's tasks by name from the API process.

The router imports this rather than ``tasks.py``, which would pull the worker's
task graph -- B's, C's and D's event names, the Slack client -- into the API
process (gap's ``enqueue.py`` does the same). Payloads carry ids only (#275).

The API process cannot ``publish`` an event: it registers no task, so
``publish`` finds no subscriber (#170). A change made in a request is announced
by a task that publishes from the worker.
"""

from __future__ import annotations

from typing import Final

from celery import current_app

DELIVER_MEETING_REPORT: Final = "autune.intelligence.deliver_meeting_report"
ANNOUNCE_MEETING_REPORT_CHANGED: Final = "autune.intelligence.announce_meeting_report_changed"


def announce_meeting_report_changed(meeting_id: str) -> None:
    """Tell the agent layer a person changed the meeting's report (#674).

    Call it after the change commits: the Report subagent reads the stored
    draft when it wakes.
    """
    current_app.send_task(ANNOUNCE_MEETING_REPORT_CHANGED, args=[meeting_id])
