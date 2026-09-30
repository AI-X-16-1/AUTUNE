"""Putting module C's own task on the queue, from outside the worker.

Separate from ``tasks.py`` for the reason ``autune_audio.enqueue`` is: the API
process imports every router at startup, and a router that reached
``tasks.py`` for the task object would pull the extractor and the embedder into
a process that never runs them. Sending by name avoids that, and the name is
declared here so ``tasks.py`` registers under the same constant.

This is not what ``autune_core.events`` forbids. That rule is about naming
*another module's* task; ``autune.gap.publish_report`` is this module's own.
"""

from __future__ import annotations

from celery import current_app

from autune_core import get_logger

log = get_logger(__name__)

PUBLISH_REPORT = "autune.gap.publish_report"
"""The name ``tasks.publish_report`` registers under."""


def enqueue_publish_report(meeting_id: str) -> None:
    """Queue a republish of the meeting's ``GapReport``.

    Called after the change it carries has committed: the task reads the rows
    when it runs, and a worker that got there first would publish the old ones.
    The message carries the meeting id and nothing else.
    """
    current_app.send_task(PUBLISH_REPORT, args=[meeting_id])
    log.info("gap_report_republish_queued", meeting_id=meeting_id)
