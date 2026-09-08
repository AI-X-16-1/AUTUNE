"""The outbound privacy boundary.

Everything here runs on data about to leave our infrastructure — Slack, Notion,
Jira, Google Calendar, any LLM API. This is the one place to enforce the rules
in docs/architecture/privacy.md, instead of trusting five modules to each
remember them.

Two guards:

``assert_masked`` refuses text that still contains recognisable personal data.
Module A masks before the first write, so anything reaching here should already
be clean; this catches the case where it is not, before the data is published.

``assert_personal_delivery`` refuses to put data that belongs to one person on
any surface other than their own DM.
"""

from __future__ import annotations

import re
from typing import Final

from autune_core.errors import PrivacyViolationError

# Unmasked shapes only. A masked value contains "*" and is filtered out below,
# so "010-****-5678" and "k***@example.com" pass while the originals do not.
_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "phone": re.compile(r"\b01[016-9][-.\s]?\d{3,4}[-.\s]?\d{4}\b"),
    "rrn": re.compile(r"\b\d{6}[-\s]?[1-4]\d{6}\b"),
    "card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "account": re.compile(r"\b\d{2,3}-\d{2,6}-\d{2,6}\b"),
}

MAX_OUTBOUND_CHARS: Final = 4000
"""A single message, not a transcript. Sending a whole meeting to a third party
is never what a feature needs."""


def find_unmasked(text: str) -> list[str]:
    """Return the categories of personal data still present in ``text``."""
    found = []
    for category, pattern in _PATTERNS.items():
        if any("*" not in match.group(0) for match in pattern.finditer(text)):
            found.append(category)
    return found


def assert_masked(text: str, *, destination: str) -> None:
    """Raise unless ``text`` is safe to send outside our infrastructure.

    The exception names the categories, never the values — an exception message
    reaches error tracking, which is itself a third party.
    """
    categories = find_unmasked(text)
    if categories:
        raise PrivacyViolationError(
            f"refusing to send unmasked personal data to {destination}",
            categories=sorted(categories),
        )


def assert_within_size(text: str, *, destination: str) -> None:
    if len(text) > MAX_OUTBOUND_CHARS:
        raise PrivacyViolationError(
            f"outbound payload to {destination} exceeds {MAX_OUTBOUND_CHARS} characters; "
            "send what the feature needs, not the whole meeting",
            length=len(text),
        )


def assert_personal_delivery(*, subject_id: str, recipient_id: str, is_direct: bool) -> None:
    """Guard data that belongs to exactly one person, such as a speaking ratio.

    Nobody but the subject may receive it — not a channel, not a manager, not an
    administrator. See docs/architecture/privacy.md section 3.
    """
    if not is_direct:
        raise PrivacyViolationError(
            "personal data may only be delivered by direct message, never to a channel"
        )
    if subject_id != recipient_id:
        raise PrivacyViolationError(
            "personal data may only be delivered to the person it describes"
        )


def check_outbound(text: str, *, destination: str) -> None:
    """Every outbound client calls this before the request leaves."""
    assert_masked(text, destination=destination)
    assert_within_size(text, destination=destination)
