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
from typing import Any, Final

from autune_core.errors import PrivacyViolationError

# The one set of personal-data shapes in the repository.
#
# Module A masks with these and this file refuses text that still matches them,
# so they have to be the same patterns or the two disagree about what personal
# data is. They did: module A fixed five of these in #125 while this file kept
# the originals, and the result was that the guard could not see the most
# ordinary shape in a Korean transcript. #126.
#
# A masked value contains "*" and matches nothing here, so "010-****-5678" and
# "k***@example.com" pass while the originals do not.

# **Digit boundaries, not word boundaries.** `\b` is a `\w`/non-`\w` edge, and
# in Python's unicode mode a Hangul syllable is `\w` -- so there is no boundary
# between `5678` and `로`. Korean attaches its particles directly to the number
# and Whisper writes them that way, which made `010-1234-5678로` match nothing
# at all.
_L: Final = r"(?<!\d)"
_R: Final = r"(?!\d)"

# Speech, not writing. The same number arrives spaced, hyphenated or run
# together depending on the sentence around it, and the shape not accepted is
# the one that leaks.
_SEP: Final = r"[-.\s]?"

# A Korean bank account is ten digits or more; a date is eight and a version
# string is eight. Counting digits is what tells them apart, and it needs no
# list of date formats to go stale.
MIN_ACCOUNT_DIGITS: Final = 10

PII_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # Longest shapes first: an RRN also looks like two number groups, and a card
    # number contains things that look like account fragments.
    #
    # The seventh digit is the century-and-sex marker and every value of it is
    # somebody: 1-4 Korean, 5-8 registered foreign national, 9-0 born in the
    # 1800s. The second group takes six to eight digits so one mis-transcribed
    # digit does not drop the whole thing to `account`, which keeps the last
    # four -- four digits of an ID number left standing.
    ("rrn", re.compile(rf"{_L}\d{{6}}{_SEP}[0-9]\d{{6,7}}{_R}")),
    ("card", re.compile(rf"{_L}(?:\d{{4}}{_SEP}){{3}}\d{{4}}{_R}")),
    # Any leading-zero prefix rather than an enumerated list. Enumerating is how
    # a regex goes stale: 070 is a common Korean VoIP range, 0505 is a safe
    # number and 080 is freephone, and none of them were in the old list.
    ("phone", re.compile(rf"{_L}0\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{4}}{_R}")),
    # +82-10-1234-5678. Without this the account pattern takes the first two
    # groups and leaves the last eight digits standing.
    ("phone", re.compile(rf"\+?82{_SEP}\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{4}}{_R}")),
    # Bank layouts vary -- 3-2-6, 6-2-6, 3-3-6 -- and get said without
    # separators as often as with. See MIN_ACCOUNT_DIGITS for what keeps this
    # from matching every date in a transcript.
    ("account", re.compile(rf"{_L}\d{{2,6}}{_SEP}\d{{2,6}}{_SEP}\d{{2,6}}{_R}")),
    # Every shaped pattern above is three groups of at most six bounded by
    # non-digits, so none can span a longer run. Two personal numbers
    # transcribed without a break matched nothing at all.
    ("digits", re.compile(rf"{_L}\d{{12,}}{_R}")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
)


def find_pii(text: str) -> list[tuple[int, int, str]]:
    """Every span of personal data in ``text``, as ``(start, end, category)``.

    The one place the patterns are applied. ``find_unmasked`` reports what this
    finds and module A hides it, so a change here reaches both.

    **Overlapping spans are all returned.** An earlier version kept the first
    span of any overlapping group and dropped the rest, which is safe for a
    guard that only needs to fire but not for the masker, because the masker
    never sees what was dropped:

        '사무실 02 1234 5678 9012 3456 이요'
            phone matches '02 1234 5678', card matches '1234 5678 9012 3456'
            dropping the card span left 12 of its 16 digits in the clear

    Merging here instead would lose the thing the masker decides from — whether
    the union came out longer than any single detector's span, which is what
    tells two run-together values apart from two detectors agreeing on one. So
    this reports what each pattern found and the caller decides: module A merges
    (``masking._resolve_overlaps``) and ``find_unmasked`` collapses to one
    category per span below.
    """
    found: list[tuple[int, int, str]] = []
    for category, pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            if MASK_CHAR in match.group():
                # Already masked. Not personal data any more.
                continue
            if category == "account" and _digit_count(match.group()) < MIN_ACCOUNT_DIGITS:
                # A date, a version, a figure said in three parts.
                continue
            found.append((match.start(), match.end(), category))
    return sorted(found, key=lambda s: (s[0], -(s[1] - s[0])))


def _most_specific(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """One category per span, for reporting rather than for masking.

    A phone number also matches the account shape and a long run of digits
    matches the catch-all, so without this a single number is reported three
    times and an exception names categories the text does not contain. The most
    specific wins, which is the order they are declared in: whichever pattern
    claimed the span first keeps it.

    Dropping a span is safe *here* and nowhere else. This feeds a guard that
    raises on the first category it finds; nothing downstream has to cover the
    text a dropped span described.
    """
    kept: list[tuple[int, int, str]] = []
    for start, end, category in spans:
        if any(start < other_end and end > other_start for other_start, other_end, _ in kept):
            continue
        kept.append((start, end, category))
    return kept


def _digit_count(value: str) -> int:
    return sum(1 for c in value if c.isdigit())


MASK_CHAR: Final = "*"

MAX_OUTBOUND_CHARS: Final = 4000
"""A single message, not a transcript. Sending a whole meeting to a third party
is never what a feature needs."""


def find_unmasked(text: str) -> list[str]:
    """The categories of personal data still present in ``text``, without repeats.

    Ordered as ``PII_PATTERNS`` is, so the same text always reports the same
    list — an exception message and a test both read this.
    """
    found = {category for _, _, category in _most_specific(find_pii(text))}
    ordered: list[str] = []
    for category, _ in PII_PATTERNS:
        if category in found and category not in ordered:
            ordered.append(category)
    return ordered


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


def strings_in(value: Any, *, addressing: frozenset[str] = frozenset()) -> list[str]:
    """Every string anywhere in a payload, minus the ones that address it.

    Rich messages put their content in a nested structure and leave a bland
    summary at the top — a Slack Block Kit ``text`` field is the notification
    preview, not the message. Checking only the top level checks the least
    important field.

    ``addressing`` names keys whose values say *where* the request goes rather
    than *what it carries*: a calendar attendee's email address is supplied by
    the feature, not extracted from a meeting, and refusing it would refuse
    every invitation.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            s
            for key, v in value.items()
            if key not in addressing
            for s in strings_in(v, addressing=addressing)
        ]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in strings_in(v, addressing=addressing)]
    return []


def check_outbound(
    payload: Any, *, destination: str, addressing: frozenset[str] = frozenset()
) -> None:
    """The last check before a request leaves. Pass the whole request body.

    Taking the body rather than a text field plus an optional extra is
    deliberate: an optional argument is a step someone forgets, and forgetting
    it here restores the exact hole this function exists to close. There is
    nothing to remember — every string in the body is checked.
    """
    parts = strings_in(payload, addressing=addressing)
    for part in parts:
        assert_masked(part, destination=destination)
    assert_within_size("".join(parts), destination=destination)
