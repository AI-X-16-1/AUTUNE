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
from .privacy import assert_personal_delivery

DESTINATION = "slack"


class SlackApi(Protocol):
    """What modules depend on. `FakeSlack` implements it for tests."""

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = ...) -> str: ...
    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str: ...
    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = ...) -> str: ...


def slack_body(
    channel: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None
) -> dict[str, Any]:
    """The body every Slack call sends, real or fake.

    One builder because the fake had its own and they drifted: the fake left
    ``thread_ts`` out, so it checked a body the client does not send and a test
    could pass on a payload production refuses. A second copy of "what we send"
    is a second answer to "what is checked".
    """
    body: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        body["blocks"] = blocks
    if thread_ts is not None:
        body["thread_ts"] = thread_ts
    return body


class SlackClient(HttpClient):
    service = "slack"

    addressing = frozenset({"channel", "thread_ts"})
    """Where the message goes, not what it says.

    `channel` is a channel id, or a user id for a DM. `thread_ts` is Slack's own
    timestamp -- `1726012345.123456` -- which is three groups of digits and is
    therefore a bank account to any pattern that reads it as content. Both are
    supplied by the feature and neither came out of a meeting, so checking them
    can only produce false refusals. `text` and `blocks` are still checked.
    """

    def __init__(self, bot_token: str) -> None:
        super().__init__(
            "https://slack.com/api",
            {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
        )

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = None) -> str:
        body = slack_body(channel, text, blocks)
        return str(self.request("POST", "/chat.postMessage", json=body).get("ts", ""))

    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str:
        body = slack_body(channel, text, thread_ts=thread_ts)
        return str(self.request("POST", "/chat.postMessage", json=body).get("ts", ""))

    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
        body = slack_body(user_id, text, blocks)
        return str(self.request("POST", "/chat.postMessage", json=body).get("ts", ""))

    def send_personal(self, *, subject_id: str, recipient_id: str, text: str) -> str:
        """Deliver data that describes exactly one person.

        Used for the speaking-ratio DM (S23). Refuses any recipient but the
        subject, and refuses a channel outright.
        """
        assert_personal_delivery(subject_id=subject_id, recipient_id=recipient_id, is_direct=True)
        return self.send_dm(recipient_id, text)
