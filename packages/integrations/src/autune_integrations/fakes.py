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
from .drive import DriveFileInfo, FileTooLargeError
from .errors import PermanentIntegrationError
from .privacy import assert_personal_delivery, check_outbound
from .slack import PostedMessage, SlackClient, slack_body


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
    updates: list[tuple[str, str, str, list[dict] | None]] = field(default_factory=list)
    """``(channel, ts, text, blocks)`` per ``update_message`` call."""
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

    def send_dm_message(
        self, user_id: str, text: str, blocks: list[dict] | None = None
    ) -> PostedMessage:
        ts = self.send_dm(user_id, text, blocks)
        return PostedMessage(channel=f"D-{user_id}", ts=ts)

    def update_message(
        self, channel: str, ts: str, text: str, blocks: list[dict] | None = None
    ) -> None:
        body = slack_body(channel, text, blocks)
        body["ts"] = ts
        check_outbound(body, destination="slack", addressing=SlackClient.addressing)
        self.updates.append((channel, ts, text, blocks))

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

    def find_pages(
        self, database_id: str, *, title_property: str, title: str, created_after: datetime
    ) -> list[str]:
        """Pages made in ``database_id`` with exactly this title, not trashed.
        The fake keeps no creation time, so ``created_after`` is not applied."""
        check_outbound({"title": title}, destination="notion")
        found = []
        for n, (db, properties) in enumerate(self.pages, start=1):
            page_id = f"page_{n}"
            value = properties.get(title_property, {}).get("title", [])
            text = value[0]["text"]["content"] if value else None
            if db == database_id and text == title and page_id not in self.archived | self.deleted:
                found.append(page_id)
        return found

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

    def trash_page(self, page_id: str) -> bool:
        if page_id in self.deleted:
            return False
        self.archived.add(page_id)
        return True

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

    # The 3LO surface (``JiraClient.for_cloud``), recorded by key.
    tasks: dict[str, dict] = field(default_factory=dict)
    accounts: dict[str, str] = field(default_factory=dict)
    """email -> account id; a missing email is someone Jira will not reveal."""
    categories: dict[str, str] = field(default_factory=dict)
    moves: list[tuple[str, str]] = field(default_factory=list)
    """Transitions actually made -- none for an issue already in the category."""
    searched: list[str] = field(default_factory=list)
    comments: dict[str, list[str]] = field(default_factory=dict)

    def find_account_id(self, email: str) -> str | None:
        self.searched.append(email)
        return self.accounts.get(email)

    def create_task(
        self,
        project_key: str,
        summary: str,
        *,
        description: str = "",
        due_date: date | None = None,
        assignee_account_id: str | None = None,
        issue_type: str = "Task",
    ) -> str:
        check_outbound({"summary": summary, "description": description}, destination="jira")
        key = f"{project_key}-{len(self.tasks) + 1}"
        self.tasks[key] = {
            "project": project_key,
            "summary": summary,
            "description": description,
            "due": due_date,
            "assignee": assignee_account_id,
        }
        self.categories[key] = "new"
        return key

    def update_task(
        self,
        issue_key: str,
        summary: str,
        *,
        due_date: date | None,
        assignee_account_id: str | None,
        keep_assignee: bool = False,
        description: str | None = None,
    ) -> bool:
        check_outbound({"summary": summary, "description": description or ""}, destination="jira")
        if issue_key not in self.tasks:
            return False
        self.tasks[issue_key].update(summary=summary, due=due_date)
        if description is not None:
            self.tasks[issue_key]["description"] = description
        if not keep_assignee:
            self.tasks[issue_key]["assignee"] = assignee_account_id
        return True

    def add_comment(self, issue_key: str, text: str) -> None:
        check_outbound({"body": text}, destination="jira")
        self.comments.setdefault(issue_key, []).append(text)

    def move_to_category(self, issue_key: str, category: str) -> bool:
        if self.categories.get(issue_key) == category:
            return True
        self.categories[issue_key] = category
        self.moves.append((issue_key, category))
        return True


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
    away: list[tuple[datetime | date, datetime | date]] = field(default_factory=list)
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

    def out_of_office(
        self, calendar_id: str, time_min: datetime, time_max: datetime
    ) -> list[tuple[datetime | date, datetime | date]]:
        """What a test put in ``away``: times only, as the real client returns."""
        return list(self.away)

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


@dataclass
class FakeDrive:
    """Answers from the files a test put in, the way Drive answers a
    ``drive.file`` grant: a file that was not picked is a 404, and a read past
    the caller's ceiling is given up on."""

    files: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    """Picked files: id -> (mime type, bytes). For a Google document the bytes
    are what its PDF export would be."""
    read: list[tuple[str, str]] = field(default_factory=list)
    """Every read made: (what, file id)."""

    def _file(self, what: str, file_id: str) -> tuple[str, bytes]:
        self.read.append((what, file_id))
        if file_id not in self.files:
            raise PermanentIntegrationError(
                "google_drive rejected the request with 404", upstream_status=404
            )
        return self.files[file_id]

    def info(self, file_id: str) -> DriveFileInfo:
        mime_type, body = self._file("info", file_id)
        stored = None if mime_type.startswith("application/vnd.google-apps.") else len(body)
        return DriveFileInfo(id=file_id, mime_type=mime_type, size=stored)

    def download(self, file_id: str, *, max_bytes: int) -> bytes:
        mime_type, body = self._file("download", file_id)
        if mime_type.startswith("application/vnd.google-apps."):
            # Drive refuses ``alt=media`` for its own document types.
            raise PermanentIntegrationError(
                "google_drive rejected the request with 403", upstream_status=403
            )
        return self._capped(body, max_bytes)

    def export_pdf(self, file_id: str, *, max_bytes: int) -> bytes:
        _mime_type, body = self._file("export_pdf", file_id)
        return self._capped(body, max_bytes)

    @staticmethod
    def _capped(body: bytes, max_bytes: int) -> bytes:
        if len(body) > max_bytes:
            raise FileTooLargeError("google_drive: the file is larger than can be shown")
        return body
