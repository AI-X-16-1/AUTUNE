"""The outbound boundary is where data leaves our infrastructure.

These tests exist because a leak here is not a bug — it is an incident.
See docs/architecture/privacy.md.
"""

from __future__ import annotations

import time

import pytest

from autune_core.errors import PrivacyViolationError
from autune_integrations import find_unmasked
from autune_integrations.base import HttpClient
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.fakes import FakeJira, FakeNotion, FakeSlack
from autune_integrations.privacy import (
    MAX_OUTBOUND_CHARS,
    assert_masked,
    assert_personal_delivery,
    check_outbound,
    find_pii,
    strings_in,
)

UNMASKED = [
    ("phone", "연락은 010-1234-5678 로 주세요"),
    ("rrn", "주민번호 900101-1234567 입니다"),
    ("card", "카드 1234-5678-9012-3456 로 결제"),
    ("email", "메일은 hong@example.com 입니다"),
]

MASKED = [
    "연락은 010-****-5678 로 주세요",
    "주민번호 900101-1****** 입니다",
    "카드 ****-****-****-3456 로 결제",
    "메일은 h***@example.com 입니다",
]


@pytest.mark.parametrize(("category", "text"), UNMASKED)
def test_unmasked_data_is_refused(category: str, text: str) -> None:
    assert category in find_unmasked(text)
    with pytest.raises(PrivacyViolationError):
        assert_masked(text, destination="slack")


@pytest.mark.parametrize("text", MASKED)
def test_masked_data_passes(text: str) -> None:
    """Masking preserves shape, so the guard must not fire on masked values."""
    assert find_unmasked(text) == []
    assert_masked(text, destination="slack")


def test_the_exception_names_categories_not_values() -> None:
    """An exception message reaches error tracking, itself a third party."""
    with pytest.raises(PrivacyViolationError) as caught:
        assert_masked("hong@example.com", destination="slack")
    assert "hong@example.com" not in str(caught.value)
    assert caught.value.details["categories"] == ["email"]


def test_a_whole_transcript_is_refused() -> None:
    """Send what the feature needs, never the whole meeting."""
    with pytest.raises(PrivacyViolationError, match="exceeds"):
        check_outbound("가" * (MAX_OUTBOUND_CHARS + 1), destination="notion")


def test_personal_data_cannot_go_to_a_channel() -> None:
    with pytest.raises(PrivacyViolationError, match="direct message"):
        assert_personal_delivery(subject_id="user_1", recipient_id="user_1", is_direct=False)


def test_personal_data_cannot_go_to_someone_else() -> None:
    """Not a teammate, not a manager, not an administrator."""
    with pytest.raises(PrivacyViolationError, match="the person it describes"):
        assert_personal_delivery(subject_id="user_1", recipient_id="user_2", is_direct=True)


def test_speaking_ratio_reaches_only_its_subject() -> None:
    slack = FakeSlack()
    slack.send_personal(subject_id="user_1", recipient_id="user_1", text="발언 비중 12%")
    assert len(slack.sent) == 1
    assert slack.sent[0].is_dm
    assert slack.channel_messages == []

    with pytest.raises(PrivacyViolationError):
        slack.send_personal(subject_id="user_1", recipient_id="user_2", text="발언 비중 12%")


def test_fakes_enforce_the_same_guards_as_real_clients() -> None:
    """A test that would have leaked must fail in tests too."""
    with pytest.raises(PrivacyViolationError):
        FakeSlack().post_message("#general", "전화번호 010-1234-5678")
    with pytest.raises(PrivacyViolationError):
        FakeJira().create_issue("AUT", "Task", "요약", "담당자 메일 hong@example.com")


# A rich message carries its content in a nested structure and leaves a bland
# summary at the top. The guard used to read only the summary.


def test_slack_blocks_are_checked_not_just_the_fallback_text() -> None:
    """Block Kit puts the message in `blocks`; `text` is the notification preview.

    A guard reading only `text` checks the least important field, and every rich
    message walks straight past it.
    """
    with pytest.raises(PrivacyViolationError):
        FakeSlack().post_message(
            "#squad",
            "액션아이템이 준비되었습니다",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "담당자 연락처 010-1234-5678"},
                }
            ],
        )


def test_the_check_reaches_the_bottom_of_a_nested_payload() -> None:
    """Block Kit nests several levels deep; one pass over the top is not enough."""
    with pytest.raises(PrivacyViolationError):
        FakeSlack().send_dm(
            "U123",
            "확인 부탁드립니다",
            blocks=[{"elements": [{"elements": [{"text": "hong@example.com"}]}]}],
        )


def test_notion_properties_are_checked_through_their_nesting() -> None:
    """A Notion property wraps its value three objects deep."""
    with pytest.raises(PrivacyViolationError):
        FakeNotion().create_page(
            "db_1", {"제목": {"title": [{"text": {"content": "010-9999-8888"}}]}}
        )


def test_a_clean_rich_message_still_goes_out() -> None:
    """The guard must not reject every structured payload it is handed."""
    slack = FakeSlack()
    slack.post_message(
        "#squad",
        "갭 리포트가 준비되었습니다",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "HIGH 2건"}}],
    )
    assert len(slack.sent) == 1


def test_size_is_measured_over_the_whole_payload() -> None:
    """Splitting a transcript across many blocks must not evade the size limit."""
    with pytest.raises(PrivacyViolationError, match="exceeds"):
        FakeSlack().post_message(
            "#squad",
            "회의 요약",
            blocks=[{"text": "가" * 500} for _ in range(20)],
        )


# The guard runs in HttpClient.request, so a client cannot skip it by forgetting
# an argument. Addressing keys are the one declared exception.


def test_an_address_the_feature_supplies_is_not_meeting_content() -> None:
    """A calendar attendee's email is where the invitation goes, not what it says.

    Checking it would refuse every invitation, so the client declares the key.
    """
    body = {
        "summary": "검색 개인화 후속",
        "attendees": [{"email": "hong@example.com"}, {"email": "kim@example.com"}],
    }
    check_outbound(body, destination="google_calendar", addressing=frozenset({"attendees"}))


def test_declaring_nothing_checks_everything() -> None:
    """Forgetting to declare an addressing key fails closed, not open."""
    body = {"attendees": [{"email": "hong@example.com"}]}
    with pytest.raises(PrivacyViolationError):
        check_outbound(body, destination="google_calendar")


def test_an_exemption_covers_only_the_key_it_names() -> None:
    """Exempting attendees must not exempt a phone number in the summary."""
    body = {
        "summary": "연락처 010-1234-5678",
        "attendees": [{"email": "hong@example.com"}],
    }
    with pytest.raises(PrivacyViolationError):
        check_outbound(body, destination="google_calendar", addressing=frozenset({"attendees"}))


def test_strings_in_skips_the_named_key_at_any_depth() -> None:
    assert strings_in(
        {"a": {"b": "keep"}, "skip": {"c": "drop"}}, addressing=frozenset({"skip"})
    ) == ["keep"]


def test_every_client_runs_the_guard_through_the_transport() -> None:
    """The check lives in HttpClient.request, so no client method can omit it."""
    import inspect

    from autune_integrations.base import HttpClient

    assert "check_outbound" in inspect.getsource(HttpClient.request)


# The guard reads the body httpx is given. httpx can be given a body five ways,
# and guarding one of them is the same bug one level up.


class _Unsent(HttpClient):
    """A client with no transport, used to run the guard and nothing else.

    Built without ``__init__`` so there is no httpx client behind it. A body the
    guard rejects raises; a body it allows runs on and fails reaching for the
    transport that is not there, which is how "allowed" is asserted below.
    """

    service = "probe"


def _guarded(**kwargs: object) -> None:
    HttpClient.request(_Unsent.__new__(_Unsent), "POST", "/anything", **kwargs)


@pytest.mark.parametrize("channel", ["json", "data", "params"])
def test_every_inspectable_channel_is_checked(channel: str) -> None:
    """Not just json. A body sent as form data leaves by the same wire."""
    with pytest.raises(PrivacyViolationError):
        _guarded(**{channel: {"note": "제 번호는 010-1234-5678 입니다"}})


@pytest.mark.parametrize("channel", ["content", "files"])
def test_channels_that_cannot_be_read_are_refused(channel: str) -> None:
    """Raw bytes cannot be shown to be free of personal data, so they do not go."""
    with pytest.raises(PermanentIntegrationError):
        _guarded(**{channel: b"anything at all"})


def test_a_clean_body_still_passes_the_guard() -> None:
    """The guard must not be the reason every request fails."""
    with pytest.raises(AttributeError):
        # Past the guard, reaching for the transport _Unsent does not have.
        _guarded(json={"note": "마스킹된 번호는 [전화번호] 입니다"})


# The six shapes that walked past this guard before #126. Every one of them is
# ordinary in a Korean meeting transcript, and the last three were invisible to
# the patterns entirely rather than hidden by a boundary.
LEAKED_BEFORE_126 = [
    "제 번호는 010-1234-5678입니다",
    "주민번호 900101-1234567이고요",
    "카드 1234-5678-9012-3456으로 결제했습니다",
    "등록번호 900101-5123456 입니다",
    "계좌는 110234567890 이에요",
    "주민 900101123456701012345678 입니다",
]


@pytest.mark.parametrize("line", LEAKED_BEFORE_126)
def test_a_particle_or_a_run_together_number_does_not_hide_it(line: str) -> None:
    """`\\b` is a `\\w` edge and a Hangul syllable is `\\w`.

    So there was no word boundary between `5678` and `입니다`, and a number with
    a particle attached — which is how Korean is written and how Whisper writes
    it — matched nothing. Module A had the same bug and fixed it in #125; this
    file kept the originals, so both layers were open on the same input.
    """
    assert find_unmasked(line) != []


@pytest.mark.parametrize("line", LEAKED_BEFORE_126)
def test_the_client_refuses_to_send_it(line: str) -> None:
    with pytest.raises(PrivacyViolationError):
        FakeSlack().post_message("#general", line)


@pytest.mark.parametrize(
    "line",
    [
        "다음 회의는 9월 18일 오후 3시 반, 405호입니다",
        "배포는 2024.01.15 예정입니다",
        "예산 100 200 300 만원으로 잡았습니다",
        "학습 데이터는 총 1,240건인데 그중 870건이에요",
    ],
)
def test_a_meeting_full_of_numbers_still_goes_out(line: str) -> None:
    """Widening the patterns has a cost in the other direction.

    A guard that refuses every date stops a team from being told when their
    meeting is, and the account shape is three groups of digits — which is also
    what a date is. `MIN_ACCOUNT_DIGITS` is what separates them.
    """
    assert find_unmasked(line) == []
    FakeSlack().post_message("#general", line)


def test_overlapping_spans_are_all_returned() -> None:
    """`find_unmasked` collapses to one category per span; `find_pii` does not.

    The guard only needs to fire, so dropping an overlapping span costs it
    nothing. The masker hides what this returns, and there dropping a span
    leaves its text in the clear — so the collapse happens in `find_unmasked`
    and the raw spans reach module A.
    """
    line = "사무실 02 1234 5678 9012 3456 이요"
    assert "card" in [category for _, _, category in find_pii(line)]
    assert find_unmasked(line) == ["phone"]


def test_each_span_is_reported_as_one_category() -> None:
    """A phone number also matches the account shape and the long-digit
    catch-all. Without resolving the overlap an exception names categories the
    text does not contain, which sends whoever reads it looking for a card
    number that was never there."""
    assert find_unmasked("제 번호는 010-1234-5678입니다") == ["phone"]
    assert find_unmasked("a@b.com 와 010-1111-2222") == ["phone", "email"]


# --- identifiers are not numbers ------------------------------------------


def test_a_generated_id_is_not_personal_data() -> None:
    """`new_id()` is a prefix plus 32 hex characters, and hex is mostly digits.

    A digit boundary reads the middle of one as a number: about one in eleven
    `usr_` ids contains a long enough run with hex letters on either side, so
    one request in eleven was refused at random. `\b` never had this problem and
    had the opposite one — it could not find the edge of a Korean particle — so
    the boundary is neither, but a class naming what actually ends a number.
    """
    from autune_core.ids import new_id

    for prefix in ("usr", "mtg", "act", "thr", "utt", "prt", "gap", "topic", "dec", "job"):
        offenders = [i for i in (new_id(prefix) for _ in range(2000)) if find_unmasked(i)]
        assert offenders == [], offenders


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("메일은minkyoung@example.com로 부탁드립니다", (3, 24)),
        ("주소는minkyoung@example.com입니다", (3, 24)),
        ("메일 minkyoung@example.com 로", (3, 24)),
        ("minkyoung@example.co.kr", (0, 23)),
    ],
)
def test_an_address_written_against_korean_is_the_address_only(
    line: str, expected: tuple[int, int]
) -> None:
    r"""`email` was the last pattern on `\b`, and the last with `\w` classes.

    Both are the same Korean bug from opposite ends. Hangul is a word
    character, so `\b` never fires between 은 and m, and `[\w.+-]+` then eats
    the Korean in front of the address:

        메일은minkyoung@example.com로  ->  메***@example.com로

    소는 was deleted from the sentence as if it were part of somebody's
    address. Changing the boundary alone does not fix it -- the greedy class
    has to stop matching Hangul first.
    """
    assert [(s, e) for s, e, c in find_pii(line) if c == "email"] == [expected]


def test_the_international_phone_pattern_has_a_left_boundary_too() -> None:
    r"""It was the one pattern without one, and hex is full of `82`.

    Every sibling pattern anchors its start; this one began `\+?82`, so it
    matched inside `utt_0f0a8ce845434317af87928215854283` — `8215854283`, read
    as a Korean country code and a number. Found by running the id test at
    thirty thousand samples rather than two hundred.
    """
    assert find_unmasked("utt_0f0a8ce845434317af87928215854283") == []
    assert find_unmasked("+82-10-1234-5678 로 연락") == ["phone"]
    assert find_unmasked("연락처 +82 10 1234 5678") == ["phone"]


def test_a_korean_particle_still_ends_a_number() -> None:
    """The other half of the same boundary. Widening it must not undo #126."""
    assert find_unmasked("010-1234-5678로 연락주세요") == ["phone"]
    assert find_unmasked("주민번호 900101-1234567이고요") == ["rrn"]


def test_a_slack_channel_and_thread_id_are_not_content() -> None:
    """They address the request. Checking them can only refuse a real one.

    `thread_ts` is `1726012345.123456` — three groups of digits, which is a bank
    account to any pattern reading it as content — and `channel` holds a user id
    on a DM.
    """
    slack = FakeSlack()
    slack.reply_in_thread("C0123456789", "1726012345.123456", "정리했습니다")
    slack.send_dm("U01234567890123", "확인 부탁드립니다")
    assert len(slack.sent) == 2


def test_the_fake_checks_the_body_the_client_sends() -> None:
    """They built the body separately and the fake's was missing `thread_ts`.

    So the fake checked something the client does not send: a test could pass on
    a payload production refuses, which is the opposite of what a fake is for.
    One builder now, used by both.
    """
    from autune_integrations.slack import slack_body

    assert set(slack_body("C1", "t", thread_ts="1726012345.123456")) == {
        "channel",
        "text",
        "thread_ts",
    }


def test_the_body_is_still_checked_when_addressing_is_exempt() -> None:
    slack = FakeSlack()
    with pytest.raises(PrivacyViolationError):
        slack.post_message("C0123456789", "연락처 010-1234-5678")


# --- a rejected match must not swallow the real one ------------------------


@pytest.mark.parametrize(
    "line",
    [
        "금액 50 1002-123-456789",
        "12 110-123-456789",
        "예산 7 1002-123-456789 로 보내주세요",
    ],
)
def test_a_figure_before_an_account_does_not_hide_it(line: str) -> None:
    """`finditer` resumes after a match, including one that was thrown away.

    The account shape matched `50 1002-123` first — nine digits, a figure,
    correctly rejected — and the scan then resumed past it, so the account that
    starts inside what was rejected was never looked at. It reached neither the
    masker nor the guard.
    """
    assert "account" in find_unmasked(line)


def test_an_oversized_payload_is_refused_before_it_is_scanned() -> None:
    """Scanning costs more than linearly, and an oversized payload is refused
    either way. `010-` twenty thousand times took 37 seconds to refuse."""
    slack = FakeSlack()
    started = time.monotonic()
    with pytest.raises(PrivacyViolationError) as caught:
        slack.post_message("C0123456789", "010-" * 20000)
    assert "length" in caught.value.details
    assert time.monotonic() - started < 1.0


# --- separator width (#162) ------------------------------------------------ #
# One separator character meant these passed the guard entirely. Not
# over-masking: nothing matched, so `assert_masked` let the text out.

SPACED_AROUND_SEPARATOR = [
    ("phone", "010 - 1234 - 5678 로 연락 주세요"),
    ("phone", "02 - 123 - 4567 이요"),
    ("phone", "+82 - 10 - 1234 - 5678"),
    ("rrn", "900101 - 1234567 입니다"),
    ("card", "1234 - 5678 - 9012 - 3456 카드요"),
]

TYPOGRAPHIC_DASH = [
    ("phone", "010–1234–5678 입니다"),  # en dash, what an editor makes of a hyphen
    ("phone", "010 — 1234 — 5678"),  # em dash
]

PARENTHESISED_AREA_CODE = [("phone", "(02)123-4567 로 전화 주세요")]

# Horizontal whitespace that is not U+0020. `[ \t]` was narrower than the
# `\s` it replaced *and* narrower than "horizontal space": a no-break space is
# what Word, HWP and Notion put between number groups, and a full-width space is
# what a Korean IME emits. Both let a complete landline through untouched -- nine
# digits, so `account` could not catch it either (#211 review).
UNUSUAL_HORIZONTAL_SPACE = [
    ("phone", "02\u00a0123\u00a04567"),  # no-break space
    ("phone", "010\u30001234\u30005678"),  # ideographic (full-width) space
    ("phone", "010\u00a0-\u00a01234\u00a0-\u00a05678"),  # NBSP around the dash
    ("rrn", "900101\u3000-\u30001234567"),
    ("card", "1234\u00a05678\u00a09012\u00a03456"),
]


@pytest.mark.parametrize(
    ("category", "text"),
    SPACED_AROUND_SEPARATOR + TYPOGRAPHIC_DASH + PARENTHESISED_AREA_CODE + UNUSUAL_HORIZONTAL_SPACE,
)
def test_a_wider_separator_is_still_the_same_number(category: str, text: str) -> None:
    assert category in {cat for _, _, cat in find_pii(text)}
    with pytest.raises(PrivacyViolationError):
        assert_masked(text, destination="slack")


# What the width cost. A transcript is dense with numbers written this way, and
# the widening was measured against these before it was made -- see #162.
NOT_PERSONAL_DATA = [
    "2024 - 2025 - 2026 로드맵",  # the case that kept `account` on the narrow one
    "2026 – 2027 예산안",
    "스프린트 12 - 13 - 14 계획",
    "10 - 20 - 30 퍼센트",
    "Q1 - Q2 - Q3 계획",
    "1 - 2 - 3 순서로",
    "매출 100 - 200 억 사이",
    "페이지 100 - 200 사이",
    "p95 는 120 - 180 ms 입니다",
    "예산은 1,234,567원입니다",
    "2026-09-10 회의록",
    "버전 1.2.3 배포합니다",
    "IP 는 192.168.10.20 입니다",
    "티켓 12345 이슈 67890 확인",
    "회의실 A - 301 호",
    "커밋 abc1234 - def5678",
    "온도 36.5 도",
    "회의는 3시 30분입니다",
    "1234)5678(9012)3456",  # `(` between groups is not a separator; only `)` is
]


@pytest.mark.parametrize("text", NOT_PERSONAL_DATA)
def test_the_wider_separator_does_not_reach_ordinary_meeting_numbers(text: str) -> None:
    """`2024 - 2025 - 2026` is three groups of two-to-six digits — the shape of
    `account` exactly. It is why that one pattern keeps the narrow separator."""
    assert find_pii(text) == []
    assert_masked(text, destination="slack")


# What the width costs, and that the cost is taken knowingly. Four groups of
# four is the shape of `card`, and the wider separator reaches it -- these are
# over-masked, not leaked. A roadmap said as four years disappears from the
# transcript and B, C and D never see that sentence; the "0 false positives"
# above was measured on three-group lists and does not cover these. Kept here
# rather than fixed so the next reader knows the trade exists; a Luhn check on
# `card` is the one thing that could tell a year list from a card number by
# something other than shape, and that is a separate decision (#212).
KNOWN_OVER_MASKING = [
    ("card", "2024 - 2025 - 2026 - 2027 로드맵"),
    ("card", "1000 - 2000 - 3000 - 4000 원"),
    ("phone", "031 - 100 - 2000 명"),
]


@pytest.mark.parametrize(("category", "text"), KNOWN_OVER_MASKING)
def test_a_four_group_list_is_over_masked_and_we_know_it(category: str, text: str) -> None:
    assert {cat for _, _, cat in find_pii(text)} == {category}


@pytest.mark.parametrize("head", ["010", "1234", "900101", "+82"])
def test_a_long_run_of_spaces_after_a_digit_is_scanned_once(head: str) -> None:
    """`[ \\t]*[-.–—)]?[ \\t]*` let the two space runs share one run of spaces,
    and the engine tried every split before failing: 900 ms at ten thousand
    spaces, quadratic in the run. Whisper emits exactly this on a silent
    stretch, and transcript masking has no length cap in front of `find_pii`.
    Per pattern at fifty thousand spaces the quadratic form takes seven to
    eleven seconds; the fixed one, about a millisecond."""
    started = time.monotonic()
    assert find_pii(head + " " * 50_000 + "x") == []
    assert time.monotonic() - started < 2.0


def test_a_match_does_not_run_across_a_line_break() -> None:
    """`\\s` would let the widened separator join two unrelated numbers. Only
    `account` can still do this, on the narrow separator it kept, and that is
    pre-existing rather than something the width introduced."""
    assert {cat for _, _, cat in find_pii("예산\n150000\n200000")} == {"account"}
