"""Google Calendar.

Owned by the Workload subagent's owner (#260, #261 section 3.1) and shared by
three callers, per #435: Briefing lists a team calendar's upcoming meetings,
Follow-up and Workload ask when people are busy, and module B puts a confirmed
action item's due date on the team calendar. Follow-up's approved meeting is
``create_event``.

**Availability is busy windows only** -- never event titles, attendees or
places, which are other people's data. ``free_busy`` is the only read that
covers people other than the calendar's owner, and Google's own free/busy
endpoint returns nothing else.

**A calendar that could not be read is not a free one.** Google answers an
unknown address, or one outside the team's Workspace domain that does not share
free/busy, with an error for that calendar and an empty busy list beside it
(#59). Read as "no busy windows", that person is free all week. ``free_busy``
returns ``None`` for them instead, so every caller has to decide what unknown
means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .base import HttpClient
from .errors import PermanentIntegrationError, TransientIntegrationError

DESTINATION = "google_calendar"

TOKEN_URL = "https://oauth2.googleapis.com/token"


class ReconnectRequiredError(PermanentIntegrationError):
    """Google refused the refresh token: revoked, expired, or the account that
    granted it is gone. Nothing retries its way out of this -- a person has to
    connect the calendar again (#428, #435)."""

    code = "integration_reconnect_required"


def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    http: httpx.Client | None = None,
) -> str:
    """A fresh access token for a stored refresh token.

    Not through ``HttpClient.request``: this is our own credential going to
    Google's token endpoint, not meeting content going to a calendar, and the
    outbound check would read a client secret as text to scan. Nothing from the
    response except the token is kept, and no part of the request or response
    reaches a log line or an exception message.
    """
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    owned = http is None
    client = http or httpx.Client(timeout=10.0)
    try:
        response = client.post(TOKEN_URL, data=body)
    except httpx.HTTPError as exc:
        raise TransientIntegrationError("google token endpoint is unreachable") from exc
    finally:
        if owned:
            client.close()
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientIntegrationError(f"google token endpoint returned {response.status_code}")
    if response.status_code >= 400:
        # invalid_grant is the documented answer for a revoked or expired
        # refresh token; any other 4xx is our own configuration, and a person
        # reconnecting is the fix for both from the team's side.
        raise ReconnectRequiredError(
            f"google refused the refresh token with {response.status_code}",
            upstream_status=response.status_code,
        )
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise PermanentIntegrationError("google token endpoint returned no access token")
    return token


@dataclass(frozen=True)
class CalendarEvent:
    """One event from a calendar the team connected -- its own meetings."""

    id: str
    summary: str
    start: datetime | date
    end: datetime | date
    attendees: list[str] = field(default_factory=list)

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


def _when(raw: dict[str, Any]) -> datetime | date:
    if "dateTime" in raw:
        return datetime.fromisoformat(raw["dateTime"])
    return date.fromisoformat(raw["date"])


class CalendarClient(HttpClient):
    service = "google_calendar"

    addressing = frozenset({"attendees", "email", "calendar_id", "items"})
    """Keys whose values address the request. An attendee's address is supplied
    by the feature, not extracted from a meeting -- checking it would refuse
    every invitation. ``items`` is ``free_busy``'s list of calendars to ask
    about, which is addresses and nothing else."""

    def __init__(self, access_token: str) -> None:
        super().__init__(
            "https://www.googleapis.com/calendar/v3",
            {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )

    # --- reads -----------------------------------------------------------------

    def list_events(
        self, calendar_id: str, time_min: datetime, time_max: datetime, *, limit: int = 50
    ) -> list[CalendarEvent]:
        """Events on the team's own calendar between two instants, recurring
        ones expanded, in start order. For Briefing's "which meeting starts
        next" (#435); not for looking at a person's calendar."""
        params = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(limit),
        }
        body = self.request("GET", f"/calendars/{calendar_id}/events", params=params)
        return [
            CalendarEvent(
                id=str(raw["id"]),
                summary=str(raw.get("summary", "")),
                start=_when(raw["start"]),
                end=_when(raw["end"]),
                attendees=[a["email"] for a in raw.get("attendees", []) if "email" in a],
            )
            for raw in body.get("items", [])
            if raw.get("status") != "cancelled"
        ]

    def free_busy(
        self, emails: list[str], time_min: datetime, time_max: datetime
    ) -> dict[str, list[tuple[datetime, datetime]] | None]:
        """Each person's busy windows between two instants -- or ``None`` for a
        person whose calendar Google could not read, which is *unknown*, not
        free (module docstring)."""
        body = self.request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "items": [{"id": email} for email in emails],
            },
        )
        calendars = body.get("calendars", {})
        answer: dict[str, list[tuple[datetime, datetime]] | None] = {}
        for email in emails:
            entry = calendars.get(email)
            if entry is None or entry.get("errors"):
                answer[email] = None
                continue
            answer[email] = [
                (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
                for b in entry.get("busy", [])
            ]
        return answer

    # --- writes ----------------------------------------------------------------

    def create_event(
        self, calendar_id: str, summary: str, start_iso: str, end_iso: str, attendees: list[str]
    ) -> str:
        body = {
            "summary": summary,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "attendees": [{"email": email} for email in attendees],
        }
        return str(
            self.request("POST", f"/calendars/{calendar_id}/events", json=body).get("id", "")
        )

    def create_all_day_event(
        self, calendar_id: str, summary: str, day: date, *, description: str = ""
    ) -> str:
        """An all-day event on ``day`` with no attendees, so nobody is sent an
        invitation -- a due date on the team calendar, not a meeting."""
        return str(
            self.request(
                "POST", f"/calendars/{calendar_id}/events", json=_all_day(summary, day, description)
            ).get("id", "")
        )

    def update_all_day_event(
        self, calendar_id: str, event_id: str, summary: str, day: date, *, description: str = ""
    ) -> bool:
        """Move or rename an all-day event. ``False`` when it is gone -- someone
        deleted it in Calendar -- so the caller can make a new one."""
        try:
            self.request(
                "PATCH",
                f"/calendars/{calendar_id}/events/{event_id}",
                json=_all_day(summary, day, description),
            )
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") in (404, 410):
                return False
            raise
        return True

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event; one already gone (404, or 410 once deleted) is done."""
        try:
            self.request("DELETE", f"/calendars/{calendar_id}/events/{event_id}")
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") not in (404, 410):
                raise


def _all_day(summary: str, day: date, description: str) -> dict[str, Any]:
    # Google's all-day end date is exclusive: an event on the 2nd ends on the 3rd.
    return {
        "summary": summary,
        "description": description,
        "start": {"date": day.isoformat()},
        "end": {"date": (day + timedelta(days=1)).isoformat()},
    }
