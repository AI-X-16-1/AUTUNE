"""Putting module A's own task on the queue, from outside the worker.

Separate from ``tasks.py`` because of what importing that module costs. It pulls
in the decoder, the diarizer and Whisper — hundreds of megabytes of torch — and
the API process needs none of it. ``apps/api`` imports every module's router at
startup, so a router that reaches ``tasks.py`` for the task object would load
the whole inference stack into a process that never runs inference.

Sending by name avoids that, and the name is declared here so ``tasks.py`` can
use the same constant for its registration. One string, two readers, no way for
them to drift.

This is not the rule ``autune_core.events`` is about. That one forbids naming
*another module's* task, because the coupling is invisible to import-linter and
the consumer belongs to somebody else. ``autune.audio.process_recording`` is
this module's own task, registered ten lines away in this module's ``tasks.py``.
"""

from __future__ import annotations

from celery import current_app

from autune_core import get_logger

log = get_logger(__name__)

PROCESS_RECORDING = "autune.audio.process_recording"
"""The name ``tasks.process_recording`` registers under."""


def enqueue_process_recording(meeting_id: str, upload_path: str) -> None:
    """Queue the pipeline for a recording already written to disk.

    Raises whatever the broker raises. The caller is inside ``storage.handover``
    and needs the failure to reach it: a recording whose task was never queued
    has nobody coming to delete it, and the ``except`` there is what deletes it
    instead.

    The path is not logged *here*. A scratch filename is not meeting content,
    but it is one ``ls`` away from a recording, and this line would be the map.
    That is a promise module A can keep only for its own log lines: Celery puts
    task arguments in the broker message and in its own failure output, so the
    path does travel outside this function — which is one of the things the
    decision issue on this handover has to settle (privacy.md section 1).
    """
    current_app.send_task(PROCESS_RECORDING, args=[meeting_id, upload_path])
    log.info("audio_process_queued", meeting_id=meeting_id)
