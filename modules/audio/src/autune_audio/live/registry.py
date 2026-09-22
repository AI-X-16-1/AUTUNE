"""Which meetings have a live socket open, in this process.

One claim per meeting. The route takes it after ``begin_live`` commits and
releases it before it sends ``ended``, so the browser's upload -- which
follows ``ended`` -- never finds the claim still held. ``service`` reads it
to refuse an upload for a meeting whose socket is still open: the status
``recording`` alone cannot tell "the socket dropped and the browser is
reconnecting" from "someone else is trying to upload over a live session".

Per process, like the sessions themselves (``environments.md``: uvicorn
``--workers 1``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autune_audio.live.session import LiveSession

_open: dict[str, LiveSession] = {}


class AlreadyOpenError(RuntimeError):
    """A second claim for a meeting that already has one."""


def claim(meeting_id: str, session: LiveSession) -> None:
    if meeting_id in _open:
        raise AlreadyOpenError(meeting_id)
    _open[meeting_id] = session


def release(meeting_id: str) -> None:
    _open.pop(meeting_id, None)


def is_open(meeting_id: str) -> bool:
    return meeting_id in _open


def open_count() -> int:
    return len(_open)


def clear() -> None:
    """Tests only."""
    _open.clear()
