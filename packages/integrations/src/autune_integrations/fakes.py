"""In-memory doubles, so modules can be written and tested without credentials.

Tests mock external services at this boundary, never with network calls.
See docs/engineering/testing.md.

The fakes run the same privacy checks as the real clients — a test that would
have leaked personal data fails here too, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

    def create_page(self, database_id: str, properties: dict) -> str:
        check_outbound({"properties": properties}, destination="notion")
        self.pages.append((database_id, properties))
        return f"page_{len(self.pages)}"


@dataclass
class FakeJira:
    issues: list[dict] = field(default_factory=list)
    transitions: list[tuple[str, str]] = field(default_factory=list)

    def create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str,
        *,
        assignee_account_id: str | None = None,
        due_date: str | None = None,
    ) -> str:
        check_outbound({"summary": summary, "description": description}, destination="jira")
        self.issues.append(
            {
                "project": project_key,
                "type": issue_type,
                "summary": summary,
                "description": description,
                "assignee_account_id": assignee_account_id,
                "due_date": due_date,
            }
        )
        return f"{project_key}-{len(self.issues)}"

    def transition(self, issue_key: str, transition_id: str) -> None:
        self.transitions.append((issue_key, transition_id))
