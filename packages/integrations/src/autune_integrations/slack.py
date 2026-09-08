"""Slack.

Every module notifies through this client, so message conventions and the
outbound privacy check live in one place.

Message style comes from docs/design/ui-spec.md section 2: status is "●" plus
text rather than an emoji, hierarchy comes from weight, at most three buttons
and only the first is primary.
"""

from __future__ import annotations

from typing import Any, Protocol

from .base import HttpClient
from .privacy import assert_personal_delivery, check_outbound

DESTINATION = "slack"


class SlackApi(Protocol):
    """What modules depend on. `FakeSlack` implements it for tests."""

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = ...) -> str: ...
    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str: ...
    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = ...) -> str: ...


class SlackClient(HttpClient):
    service = "slack"

    def __init__(self, bot_token: str) -> None:
        super().__init__(
            "https://slack.com/api",
            {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
        )

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = None) -> str:
        check_outbound(text, destination=DESTINATION)
        body: dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            body["blocks"] = blocks
        return str(self.request("POST", "/chat.postMessage", json=body).get("ts", ""))

    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str:
        check_outbound(text, destination=DESTINATION)
        body = {"channel": channel, "text": text, "thread_ts": thread_ts}
        return str(self.request("POST", "/chat.postMessage", json=body).get("ts", ""))

    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
        check_outbound(text, destination=DESTINATION)
        body: dict[str, Any] = {"channel": user_id, "text": text}
        if blocks:
            body["blocks"] = blocks
        return str(self.request("POST", "/chat.postMessage", json=body).get("ts", ""))

    def send_personal(self, *, subject_id: str, recipient_id: str, text: str) -> str:
        """Deliver data that describes exactly one person.

        Used for the speaking-ratio DM (S23). Refuses any recipient but the
        subject, and refuses a channel outright.
        """
        assert_personal_delivery(subject_id=subject_id, recipient_id=recipient_id, is_direct=True)
        return self.send_dm(recipient_id, text)
