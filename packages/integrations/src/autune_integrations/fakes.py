"""In-memory doubles, so modules can be written and tested without credentials.

Tests mock external services at this boundary, never with network calls.
See docs/engineering/testing.md.

The fakes run the same privacy checks as the real clients — a test that would
have leaked personal data fails here too, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .calendar import CalendarClient, CalendarEvent
from .errors import PermanentIntegrationError
from .privacy import assert_personal_delivery, check_outbound
from .slack import SlackClient, slack_body


@dataclass
class SentMessage:
    channel: str
    text: str
    thread_ts: str | None = None
    is_dm: bool = False


@dataclass
class FakeSlack:
    """Implements the SlackApi protocol. Records instead of sending."""

    sent: list[SentMessage] = field(default_factory=list)
    _ts: int = 0

    def _next_ts(self) -> str:
        self._ts += 1
        return f"{self._ts}.000000"

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = None) -> str:
        check_outbound(
            slack_body(channel, text, blocks),
            destination="slack",
            addressing=SlackClient.addressing,
        )
        self.sent.append(SentMessage(channel=channel, text=text))
        return self._next_ts()

    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str:
        check_outbound(
            slack_body(channel, text, None, thread_ts),
            destination="slack",
            addressing=SlackClient.addressing,
        )
        self.sent.append(SentMessage(channel=channel, text=text, thread_ts=thread_ts))
        return self._next_ts()

    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
        check_outbound(
            slack_body(user_id, text, blocks),
            destination="slack",
            addressing=SlackClient.addressing,
        )
        self.sent.append(SentMessage(channel=user_id, text=text, is_dm=True))
        return self._next_ts()

    def send_personal(self, *, subject_id: str, recipient_id: str, text: str) -> str:
        assert_personal_delivery(subject_id=subject_id, recipient_id=recipient_id, is_direct=True)
        return self.send_dm(recipient_id, text)

    @property
    def channel_messages(self) -> list[SentMessage]:
        return [m for m in self.sent if not m.is_dm]


@dataclass
class FakeNotion:
    pages: list[tuple[str, dict]] = field(default_factory=list)
    updates: list[tuple[str, dict]] = field(default_factory=list)
    """``(page_id, properties)`` for every ``update_page`` call, in order."""
    deleted: set[str] = field(default_factory=set)
    """Page ids deleted in Notion: an update is refused with 404."""
    archived: set[str] = field(default_factory=set)
    """Page ids archived or in the trash: an update is refused with 400, as
    Notion refuses it."""

    def create_page(self, database_id: str, properties: dict) -> str:
        check_outbound({"properties": properties}, destination="notion")
        self.pages.append((database_id, properties))
        return f"page_{len(self.pages)}"

    def update_page(self, page_id: str, properties: dict) -> None:
        check_outbound({"properties": properties}, destination="notion")
        if page_id in self.deleted:
            raise PermanentIntegrationError(
                "notion rejected the request with 404", upstream_status=404
            )
        if page_id in self.archived:
            raise PermanentIntegrationError(
                "notion rejected the request with 400", upstream_status=400
            )
        self.updates.append((page_id, properties))

    def page_state(self, page_id: str) -> str:
        if page_id in self.deleted:
            return "deleted"
        return "archived" if page_id in self.archived else "live"


@dataclass
class FakeJira:
    issues: list[dict] = field(default_factory=list)
    transitions: list[tuple[str, str]] = field(default_factory=list)

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str
    ) -> str:
        check_outbound({"summary": summary, "description": description}, destination="jira")
        self.issues.append(
            {
                "project": project_key,
                "type": issue_type,
                "summary": summary,
                "description": description,
            }
        )
        return f"{project_key}-{len(self.issues)}"

    def transition(self, issue_key: str, transition_id: str) -> None:
        self.transitions.append((issue_key, transition_id))


@dataclass
class FakeCalendar:
    """Records all-day events by id and answers reads from what a test put in.

    ``busy`` maps an address to its busy windows; an address missing from it is
    one Google could not read, so ``free_busy`` answers ``None`` for it -- the
    same "unknown, not free" rule as the real client."""

    events: dict[str, dict] = field(default_factory=dict)
    listed: list[CalendarEvent] = field(default_factory=list)
    changed: list[CalendarEvent] = field(default_factory=list)
    busy: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    created: int = 0
    """Events ever made, so an id is never reused -- not even after a test
    empties ``events`` to stand for someone deleting them in Calendar."""

    def list_events(
        self, calendar_id: str, time_min: datetime, time_max: datetime, *, limit: int = 50
    ) -> list[CalendarEvent]:
        return self.listed[:limit]

    def changed_events(
        self,
        calendar_id: str,
        *,
        updated_min: datetime,
        tag: tuple[str, str],
        max_pages: int = 10,
    ) -> list[CalendarEvent]:
        """What a test put in ``changed``, filtered by the tag the way Google does."""
        return [e for e in self.changed if e.private.get(tag[0]) == tag[1]]

    def free_busy(
        self, emails: list[str], time_min: datetime, time_max: datetime
    ) -> dict[str, list[tuple[datetime, datetime]] | None]:
        return {email: self.busy.get(email) for email in emails}

    def create_event(
        self, calendar_id: str, summary: str, start_iso: str, end_iso: str, attendees: list[str]
    ) -> str:
        """A timed meeting with invitees (Follow-up's approved meeting), checked
        with the real client's ``addressing`` so an invitee's address is exempt
        and everything else is not."""
        body = {
            "summary": summary,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "attendees": [{"email": email} for email in attendees],
        }
        check_outbound(body, destination="google_calendar", addressing=CalendarClient.addressing)
        self.created += 1
        event_id = f"evt_{self.created}"
        self.events[event_id] = {"calendar": calendar_id, **body}
        return event_id

    def create_all_day_event(
        self,
        calendar_id: str,
        summary: str,
        day: date,
        *,
        description: str = "",
        private: dict[str, str] | None = None,
    ) -> str:
        check_outbound(
            {"summary": summary, "description": description}, destination="google_calendar"
        )
        self.created += 1
        event_id = f"evt_{self.created}"
        self.events[event_id] = {
            "calendar": calendar_id,
            "summary": summary,
            "day": day,
            "description": description,
            "private": dict(private or {}),
        }
        return event_id

    def update_all_day_event(
        self, calendar_id: str, event_id: str, summary: str, day: date, *, description: str = ""
    ) -> bool:
        check_outbound(
            {"summary": summary, "description": description}, destination="google_calendar"
        )
        if event_id not in self.events:
            return False
        self.events[event_id].update(summary=summary, day=day, description=description)
        return True

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        if self.events.pop(event_id, None) is not None:
            self.deleted.append(event_id)
