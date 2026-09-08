"""Google Calendar.

Used by module D for scheduling the next meeting (S25) and, in Phase 2, by the
proactive agent for room booking (S31, S32).

Availability lookups return busy windows only — never event titles or
attendees, which are other people's data.
"""

from __future__ import annotations

from .base import HttpClient
from .privacy import check_outbound

DESTINATION = "google_calendar"


class CalendarClient(HttpClient):
    service = "google_calendar"

    def __init__(self, access_token: str) -> None:
        super().__init__(
            "https://www.googleapis.com/calendar/v3",
            {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )

    def create_event(
        self, calendar_id: str, summary: str, start_iso: str, end_iso: str, attendees: list[str]
    ) -> str:
        check_outbound(summary, destination=DESTINATION)
        body = {
            "summary": summary,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "attendees": [{"email": email} for email in attendees],
        }
        return str(
            self.request("POST", f"/calendars/{calendar_id}/events", json=body).get("id", "")
        )
