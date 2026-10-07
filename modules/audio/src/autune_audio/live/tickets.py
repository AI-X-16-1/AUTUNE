"""One-use tickets for a live socket opened on another host than the page.

The session cookie belongs to the page's host, so a socket on the API's own
address (``NEXT_PUBLIC_LIVE_URL`` on the web side) arrives without it, and the
page cannot read the HttpOnly cookie to put in ``hello``. The page asks for a
ticket over its own origin instead, where the cookie goes, and sends that.

**A ticket is not a session token.** It is a random string this process
remembers, not a JWT: ``current_user`` cannot read it, so it opens no API
route -- not even the one that issues tickets, so a ticket cannot be traded
for a fresher one. It opens one thing: the ``hello`` of the meeting it was
issued for, once, within ``TTL_S`` of being issued. A first version handed
out a one-minute session token instead, which any route accepted as a bearer
and which could renew itself until sign-out (review of #982).

**Signing out ends it.** The ticket remembers the person's
``sessions_valid_from`` as it was at issue; a sign-out moves it, and the
ticket no longer matches. That is a comparison, not a second copy of core's
rule for when a token is ended.

Per process, like ``registry``: the request that issues a ticket and the
socket that spends it must reach the same API process, as a live
session must; the API runs one uvicorn process.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime

PREFIX = "lt_"
"""Marks a ``hello`` token as a ticket rather than a session token."""

TTL_S = 60
"""Long enough for the page to open the socket right after asking; the
socket spends the ticket at ``hello`` and never needs it again."""


@dataclass(frozen=True)
class Ticket:
    user_id: str
    meeting_id: str
    sessions_valid_from: datetime | None
    expires_at: float
    """``time.monotonic()`` seconds."""


_tickets: dict[str, Ticket] = {}
# Issued from a sync route (FastAPI's threadpool), spent on the event loop.
_lock = threading.Lock()


def issue(*, user_id: str, meeting_id: str, sessions_valid_from: datetime | None) -> str:
    now = time.monotonic()
    ticket = PREFIX + secrets.token_urlsafe(32)
    with _lock:
        # Unspent tickets would otherwise stay for the life of the process.
        for stale in [key for key, held in _tickets.items() if held.expires_at <= now]:
            del _tickets[stale]
        _tickets[ticket] = Ticket(
            user_id=user_id,
            meeting_id=meeting_id,
            sessions_valid_from=sessions_valid_from,
            expires_at=now + TTL_S,
        )
    return ticket


def redeem(ticket: str, *, meeting_id: str) -> Ticket | None:
    """The ticket, spent, or ``None`` if it opens nothing here.

    Spent on any attempt, the wrong meeting included: a ticket is tried once."""
    with _lock:
        held = _tickets.pop(ticket, None)
    if held is None or held.expires_at <= time.monotonic() or held.meeting_id != meeting_id:
        return None
    return held


def is_ticket(token: str) -> bool:
    return token.startswith(PREFIX)


def clear() -> None:
    """Tests only."""
    with _lock:
        _tickets.clear()
