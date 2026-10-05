"""Gmail -- sending a message as the person whose grant it is (#552).

``gmail.send`` only: this client can send and cannot read anything in a
mailbox. Reading one is #431's question and is not here.

**The plain text is checked, not the request.** Gmail takes a message as an
RFC 2822 document in base64url (``raw``), which the outbound guard cannot read:
checking the request as sent would scan base64 and pass anything. So the
subject and body are checked as text before they are encoded, and ``raw`` is
declared as addressing for the request itself.

**The recipient is addressing.** The address is supplied by the feature -- the
inviter typed it -- not extracted from a meeting, and checking it would refuse
every message, as with Calendar's ``attendees``.

**``unchecked`` is for values Autune made, never for anything a person said.**
An invitation link carries a random token; a token can contain a run of digits
that reads as a phone number, and refusing the invitation for it would be a
coin toss nobody could fix. The caller names such values, and they are taken
out of the text before it is checked -- the rest of the message still is.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from email.message import EmailMessage

from .base import HttpClient
from .privacy import check_outbound

DESTINATION = "gmail"


class GmailClient(HttpClient):
    service = DESTINATION

    addressing = frozenset({"raw"})
    """The encoded message. What it carries is checked as text by ``send``
    before it is encoded (module docstring)."""

    def __init__(self, access_token: str) -> None:
        super().__init__(
            "https://gmail.googleapis.com/gmail/v1",
            {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )

    def send(self, *, to: str, subject: str, body: str, unchecked: Iterable[str] = ()) -> str:
        """Send a plain-text message from the grant's own account; Gmail's id
        for it. ``From`` is left to Gmail, which sets the account's address."""
        checked = body
        for value in unchecked:
            if value:
                checked = checked.replace(value, "")
        check_outbound({"subject": subject, "body": checked}, destination=self.service)

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        answer = self.request("POST", "/users/me/messages/send", json={"raw": raw})
        return str(answer.get("id", ""))
