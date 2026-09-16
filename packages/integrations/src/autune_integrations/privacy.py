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
# The class is wider than digits on purpose. A digit boundary alone reads the
# middle of an identifier as a number: `new_id()` makes `usr_` plus 32 hex
# characters, and about one in eleven of those contains a long enough run of
# digits with hex letters on either side. `\b` did not have this problem and had
# the opposite one -- it could not see the edge of a Korean particle -- so the
# fix is neither boundary but a class that names what actually ends a number:
# not a digit, not an ASCII letter, not an underscore. A Korean syllable is none
# of those, so `010-1234-5678로` still matches; `usr_a1234567890123b` no longer
# does, from either side.
_EDGE: Final = r"0-9A-Za-z_"
_L: Final = rf"(?<![{_EDGE}])"
_R: Final = rf"(?![{_EDGE}])"

# Speech, not writing. The same number arrives spaced, hyphenated or run
# together depending on the sentence around it, and the shape not accepted is
# the one that leaks.
#
# One separator character was too few (#162). A transcript writes `010 - 1234 -
# 5678` with spaces around the hyphen, an editor turns the hyphen into an en
# dash, and a landline arrives as `(02)123-4567`; none of those matched at all,
# so a phone number, a registration number and a card number each passed the
# guard in an ordinary written form. Horizontal space only -- `\s` would let a
# match run across a line break and join two unrelated numbers.
#
# The shape of the expression matters as much as its characters. Written as
# `[ \t]*[-.–—)]?[ \t]*`, the two space runs share the same spaces when there
# is no separator between them, and the engine tries every split of a run of n
# spaces before giving up: quadratic per start position, between a third of a
# second and a second for a digit followed by ten thousand spaces, which is
# what Whisper emits on a silent stretch, and per pattern. The second run is
# allowed only *after* a separator, so a run of spaces has one parse.
#
# `)` alone, not `()`: an opening parenthesis stands before a number
# (`(02)123-4567` starts matching at the `0`), never between its groups, and a
# character in this class is one more thing that can join two groups.
_SEP: Final = r"[ \t]*(?:[-.–—)][ \t]*)?"

# The account catch-all keeps the narrow one, and this is the whole reason the
# two exist separately. `account` is three groups of two-to-six digits, which is
# also the shape of `2024 - 2025 - 2026`; widening its separator is what turns a
# list of years into a bank account, measured as the only false positive the
# change produced. The structured patterns can afford the spaces because their
# shapes are specific enough to say no on their own -- a phone number starts
# with a zero, an RRN is 6+7, a card is four groups of four.
#
# It keeps `\s` rather than following `_SEP` to horizontal space, which is not
# an oversight: `account` already joins numbers across a line break on `main`
# (`예산\n150000\n200000` comes back as one account), and narrowing it here
# would be a second decision riding along in a file that needs five approvals.
# Tracked separately. This change makes that case strictly smaller -- `rrn` no
# longer spans the break, so the same text matches one pattern instead of two.
_SEP_TIGHT: Final = r"[-.\s]?"

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
    # four -- four digits of an ID number left standing. `[0-9]\d{5,7}` is six
    # to eight; it was written `\d{6,7}`, which is seven to eight, so the
    # dropped-digit case the comment describes fell through to `account` and
    # left `**3456` standing. The cost of the extra digit: a twelve-digit run
    # said without separators now matches here first, so an account number said
    # that way keeps one digit rather than none. One digit of an account for
    # four digits of a national ID is the trade this file makes everywhere.
    ("rrn", re.compile(rf"{_L}\d{{6}}{_SEP}[0-9]\d{{5,7}}{_R}")),
    ("card", re.compile(rf"{_L}(?:\d{{4}}{_SEP}){{3}}\d{{4}}{_R}")),
    # Any leading-zero prefix rather than an enumerated list. Enumerating is how
    # a regex goes stale: 070 is a common Korean VoIP range, 0505 is a safe
    # number and 080 is freephone, and none of them were in the old list.
    ("phone", re.compile(rf"{_L}0\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{4}}{_R}")),
    # +82-10-1234-5678. Without this the account pattern takes the first two
    # groups and leaves the last eight digits standing.
    ("phone", re.compile(rf"{_L}\+?82{_SEP}\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{4}}{_R}")),
    # Every shaped pattern above is three groups of at most six bounded by
    # non-digits, so none can span a longer run. Two personal numbers
    # transcribed without a break matched nothing at all.
    #
    # **Above `account` on purpose.** `account`'s separators are optional, so it
    # also covers a run-together twelve-to-eighteen-digit run -- the same span,
    # the same length -- and on a tie the first pattern declared wins. Below
    # `account` this rule only ever reached nineteen digits and up, and
    # `900101123456712` came out `***********6712` with the tail of a national
    # ID standing. Declared first, the rule that keeps nothing wins the tie. An
    # account said *with* separators is not matched here at all and still keeps
    # its last four.
    ("digits", re.compile(rf"{_L}\d{{12,}}{_R}")),
    # Bank layouts vary -- 3-2-6, 6-2-6, 3-3-6 -- and get said without
    # separators as often as with. See MIN_ACCOUNT_DIGITS for what keeps this
    # from matching every date in a transcript.
    ("account", re.compile(rf"{_L}\d{{2,6}}{_SEP_TIGHT}\d{{2,6}}{_SEP_TIGHT}\d{{2,6}}{_R}")),
    # The only pattern that still used `\b`, and the only one whose character
    # classes were `\w`. Both are the same Korean bug from opposite ends:
    # Hangul is a word character, so `\b` never fires between 은 and m, and
    # `[\w.+-]+` then eats the Korean in front of the address.
    # `메일은minkyoung@example.com로` came out `메***@example.com로` -- 소는 was
    # deleted from the sentence as if it were part of somebody's address.
    # Changing the boundary alone does not fix it: the greedy class has to stop
    # being able to match Hangul, and then the boundary has to stop being `\b`.
    # An address is ASCII; a local part in Hangul is not a thing Whisper writes.
    (
        "email",
        re.compile(rf"{_L}[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+{_R}"),
    ),
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
        # Not `finditer`. A rejected match has to be retried one character later,
        # because `finditer` resumes after it and the real value can start
        # inside what was rejected:
        #
        #     금액 50 1002-123-456789
        #       account first matches `50 1002-123` -- nine digits, a figure,
        #       correctly rejected -- and finditer then resumes past `123`, so
        #       `1002-123-456789` was never looked at. It reached neither the
        #       masker nor the guard.
        position = 0
        while (match := pattern.search(text, position)) is not None:
            rejected = MASK_CHAR in match.group() or (
                # A date, a version, a figure said in three parts.
                category == "account" and _digit_count(match.group()) < MIN_ACCOUNT_DIGITS
            )
            if rejected:
                position = match.start() + 1
                continue
            found.append((match.start(), match.end(), category))
            position = match.end()
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
    # Size first. Scanning is the expensive half and its cost grows faster than
    # the input does, so an oversized payload -- which is refused either way --
    # should be refused before it is scanned rather than after. It also bounds
    # what the patterns ever see to MAX_OUTBOUND_CHARS.
    assert_within_size("".join(parts), destination=destination)
    for part in parts:
        assert_masked(part, destination=destination)
