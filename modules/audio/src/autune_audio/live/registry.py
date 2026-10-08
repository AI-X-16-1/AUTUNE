"""Which meetings have a live socket open, in this process.

One claim per meeting. The route takes it after ``begin_live`` commits and
releases it the moment it reads ``stop`` (or the session limit strikes), before
the last segment is transcribed, so the browser's upload -- which follows
``ended``, or gives up waiting for it after 15 s -- never finds the claim
still held. ``service`` reads it to refuse somebody else's upload for a
meeting whose socket is still open, and lets the claim's own person upload
over it (``give_up_for_upload``): the status
``recording`` alone cannot tell "the socket dropped and the browser is
reconnecting" from "someone else is trying to upload over a live session".

Per process, like the sessions themselves (``environments.md``: uvicorn
``--workers 1``) -- so the refusal in ``start_transcription`` only sees a
claim held in the same process; a second worker process would not see it
and this registry alone would not stop the race, which is why the
``--workers 1`` rule in ``environments.md`` is load-bearing, not a default
left in place. There is also a known, accepted gap the size of one
uncontended lock acquisition: an upload that takes the meeting row's lock
in ``start_transcription`` between the hello's ``session_scope`` commit and
the following ``registry.claim`` call sees no claim yet and is not refused
by it -- ``start_transcription``'s own row lock still stops it from racing
a second upload, just not from racing that specific hello.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autune_audio.live.session import LiveSession


@dataclass(frozen=True)
class _Claim:
    session: LiveSession
    user_id: str


_open: dict[str, _Claim] = {}


class AlreadyOpenError(RuntimeError):
    """A second claim for a meeting that already has one."""


def claim(meeting_id: str, session: LiveSession, *, user_id: str) -> None:
    if meeting_id in _open:
        raise AlreadyOpenError(meeting_id)
    _open[meeting_id] = _Claim(session=session, user_id=user_id)


def release(meeting_id: str, session: LiveSession) -> None:
    """Drop the claim if ``session`` holds it. A socket that let go early (on
    ``stop``, or to its own person's upload) still runs its ``finally`` later,
    and by then a newer socket may hold the meeting (#1019 review)."""
    held = _open.get(meeting_id)
    if held is not None and held.session is session:
        del _open[meeting_id]


def give_up_for_upload(meeting_id: str, *, user_id: str) -> bool:
    """Let the claim's own person upload over it: drop it and say so.

    The claim stops *somebody else's* upload landing under a live socket. Its
    own person uploads when they stop, and the server may not have read that
    ``stop``: the first session after a deploy sits in the model's warm-up,
    which reads no message, and a socket can drop without the server hearing
    it. The recording they upload is the one that gets transcribed. The socket
    left behind ends on its own; its ``finally`` finds the claim gone."""
    held = _open.get(meeting_id)
    if held is None or held.user_id != user_id:
        return False
    del _open[meeting_id]
    return True


def holder(meeting_id: str) -> str | None:
    held = _open.get(meeting_id)
    return held.user_id if held is not None else None


def is_open(meeting_id: str) -> bool:
    return meeting_id in _open


def open_count() -> int:
    return len(_open)


def clear() -> None:
    """Tests only."""
    _open.clear()
