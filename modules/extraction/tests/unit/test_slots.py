"""Step 3's rules: the speaker is the assignee, and a Korean date phrase is a day.

Pure functions, no session. Every date below resolves against a meeting held on
Wednesday 2026-09-09, whose week runs Monday 09-07 to Sunday 09-13.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from autune_extraction.slots import Assignee, DueDate, assignee_of, meeting_day, parse_due

WEDNESDAY = date(2026, 9, 9)


@pytest.mark.parametrize(
    ("text", "phrase", "due"),
    [
        ("오늘 안에 보내드리겠습니다", "오늘", date(2026, 9, 9)),
        ("내일까지 정리하겠습니다", "내일", date(2026, 9, 10)),
        ("모레 공유드리겠습니다", "모레", date(2026, 9, 11)),
        ("글피까지 할게요", "글피", date(2026, 9, 12)),
        # A bare weekday is the next one after the meeting.
        ("금요일까지 드리겠습니다", "금요일", date(2026, 9, 11)),
        ("월요일에 보고드리겠습니다", "월요일", date(2026, 9, 14)),
        ("수요일까지 하겠습니다", "수요일", date(2026, 9, 16)),
        # A week, then a day.
        ("이번 주 금요일까지 하겠습니다", "이번 주 금요일", date(2026, 9, 11)),
        ("다음 주 화요일까지 올리겠습니다", "다음 주 화요일", date(2026, 9, 15)),
        ("다음주 월요일에 할게요", "다음주 월요일", date(2026, 9, 14)),
        ("다다음 주 월요일까지 하겠습니다", "다다음 주 월요일", date(2026, 9, 21)),
        ("차주 수요일까지 드리겠습니다", "차주 수요일", date(2026, 9, 16)),
        # A week and no day: the working week's end.
        ("이번 주까지 끝내겠습니다", "이번 주", date(2026, 9, 11)),
        ("다음 주 중으로 하겠습니다", "다음 주", date(2026, 9, 18)),
        # Weekends end on Sunday.
        ("주말까지 보겠습니다", "주말", date(2026, 9, 13)),
        ("이번 주말에 정리하겠습니다", "이번 주말", date(2026, 9, 13)),
        ("다음 주말까지 하겠습니다", "다음 주말", date(2026, 9, 20)),
        # Months.
        ("월말까지 드리겠습니다", "월말", date(2026, 9, 30)),
        ("이번 달 말까지 하겠습니다", "이번 달 말", date(2026, 9, 30)),
        ("다음 달 말까지 하겠습니다", "다음 달 말", date(2026, 10, 31)),
        ("다음 달 3일까지 하겠습니다", "다음 달 3일", date(2026, 10, 3)),
        # Named days.
        ("9월 20일까지 하겠습니다", "9월 20일", date(2026, 9, 20)),
        ("3월 2일까지 하겠습니다", "3월 2일", date(2027, 3, 2)),
        ("9/25까지 드리겠습니다", "9/25", date(2026, 9, 25)),
        ("2026-10-01까지 배포하겠습니다", "2026-10-01", date(2026, 10, 1)),
        ("15일까지 하겠습니다", "15일", date(2026, 9, 15)),
        ("5일까지 하겠습니다", "5일", date(2026, 10, 5)),
        # Counted from the meeting.
        ("3일 후에 드리겠습니다", "3일 후", date(2026, 9, 12)),
        ("2주 뒤까지 하겠습니다", "2주 뒤", date(2026, 9, 23)),
        ("일주일 안에 하겠습니다", "일주일 안에", date(2026, 9, 16)),
        ("이틀 뒤에 공유하겠습니다", "이틀 뒤", date(2026, 9, 11)),
    ],
)
def test_a_phrase_resolves_to_a_day(text: str, phrase: str, due: date) -> None:
    assert parse_due(text, WEDNESDAY) == DueDate(text=phrase, date=due)


def test_an_utterance_with_no_date_has_none() -> None:
    assert parse_due("제가 정리해서 공유드리겠습니다", WEDNESDAY) is None


def test_the_first_phrase_wins() -> None:
    """The rest is usually what happens after the thing promised."""
    due = parse_due("다음 주 금요일까지 하고 월요일에 공유하겠습니다", WEDNESDAY)

    assert due == DueDate(text="다음 주 금요일", date=date(2026, 9, 18))


def test_a_masked_phone_number_is_not_a_date() -> None:
    """Masked text is what arrives; its digits and slashes must not read as a day."""
    assert parse_due("010-****-5678로 연락드리겠습니다", WEDNESDAY) is None


def test_a_day_that_does_not_exist_keeps_the_phrase_and_no_date() -> None:
    assert parse_due("2월 30일까지 하겠습니다", WEDNESDAY) == DueDate(text="2월 30일", date=None)


def test_without_the_meetings_day_a_relative_phrase_has_no_date() -> None:
    """The upload time is not the meeting time; guessing would move every 내일."""
    assert parse_due("금요일까지 하겠습니다", None) == DueDate(text="금요일", date=None)


def test_without_the_meetings_day_an_absolute_date_still_resolves() -> None:
    assert parse_due("2026-10-01까지 하겠습니다", None) == DueDate(
        text="2026-10-01", date=date(2026, 10, 1)
    )


# --- the meeting's day -----------------------------------------------------------


def test_the_meetings_day_is_the_day_in_korea() -> None:
    """23:30 UTC on the 8th is 08:30 on the 9th in Seoul -- a morning meeting."""
    assert meeting_day(datetime(2026, 9, 8, 23, 30, tzinfo=UTC)) == WEDNESDAY


def test_a_naive_start_time_is_read_as_utc() -> None:
    assert meeting_day(datetime(2026, 9, 8, 23, 30)) == WEDNESDAY


def test_an_aware_start_time_in_another_zone_is_converted() -> None:
    plus_two = timezone(timedelta(hours=2))
    assert meeting_day(datetime(2026, 9, 9, 1, 0, tzinfo=plus_two)) == WEDNESDAY


def test_no_start_time_is_no_day() -> None:
    assert meeting_day(None) is None


# --- the assignee ----------------------------------------------------------------


KNOWN = {"user_001"}


def test_an_identified_speaker_is_the_assignee() -> None:
    assert assignee_of("user_001", "김민경", known=KNOWN) == Assignee(
        user_id="user_001", label=None
    )


def test_an_unidentified_speaker_keeps_only_the_label() -> None:
    """'Speaker 2' waits for a person to say who that was."""
    assert assignee_of(None, "Speaker 2", known=KNOWN) == Assignee(user_id=None, label="Speaker 2")


def test_an_id_that_is_not_an_account_is_treated_as_unidentified() -> None:
    """``assignee_id`` is a foreign key to ``users``; anything else would fail the
    insert and lose every item of the meeting with it."""
    assert assignee_of("par_7", "Speaker 1", known=KNOWN) == Assignee(
        user_id=None, label="Speaker 1"
    )


def test_a_well_formed_id_that_no_longer_exists_is_treated_as_unidentified() -> None:
    """The prefix alone did not promise the foreign key would hold: a deleted
    account's id is still ``user_``-shaped (PARKJAEKYUNG0525, #152)."""
    assert assignee_of("user_gone", "김민경", known=KNOWN) == Assignee(user_id=None, label="김민경")


# --- what is not a deadline (review of #152) -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1/3 정도는 끝났고 나머지는 제가 하겠습니다",
        "일주일에 한 번씩 확인하겠습니다",
        "이틀 전에 보냈는데 다시 확인하겠습니다",
        "3일 전에 말씀드린 건 제가 정리하겠습니다",
        "이번 주 월요일에 말씀드린 대로 하겠습니다",
        "9월 1일에 공유드린 대로 진행하겠습니다",
    ],
)
def test_a_fraction_a_frequency_or_the_past_is_not_a_due_date(text: str) -> None:
    """Said on Wednesday 09-09. None of these is a day the work is due by."""
    assert parse_due(text, WEDNESDAY) is None


def test_a_past_phrase_is_skipped_and_the_next_one_taken() -> None:
    due = parse_due("9월 1일에 말씀드렸고 9월 20일까지 드리겠습니다", WEDNESDAY)

    assert due == DueDate(text="9월 20일", date=date(2026, 9, 20))


def test_a_skipped_phrase_takes_its_weekday_with_it() -> None:
    """ "이번 주 월요일" is in the past; its "월요일" must not then be read alone
    as next Monday."""
    assert parse_due("이번 주 월요일에 말한 거 제가 하겠습니다", WEDNESDAY) is None


def test_this_week_said_on_a_saturday_has_already_ended() -> None:
    saturday = date(2026, 9, 12)

    assert parse_due("이번 주까지 하겠습니다", saturday) is None


def test_a_named_day_long_past_is_next_years() -> None:
    assert parse_due("3월 2일까지 하겠습니다", WEDNESDAY) == DueDate(
        text="3월 2일", date=date(2027, 3, 2)
    )


# --- what was said of the past (review of #159) ---------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # The day data was sent, not a day anything is due.
        "6월 1일에 이미 전달드렸는데 다시 정리하겠습니다",
        # The Monday it was talked about, not next Monday.
        "월요일에 말씀드렸던 거 제가 다시 정리하겠습니다",
        "월요일에 회의 있었는데 제가 정리하겠습니다",
        "화요일에 공유했고 제가 다시 보겠습니다",
        # A day long past with no deadline word is the past, not next year's.
        "6월 1일 자료 기준으로 정리하겠습니다",
    ],
)
def test_a_date_said_of_the_past_is_not_a_due_date(text: str) -> None:
    """Said on Wednesday 09-09, each of these would read as a day after the
    meeting if taken forward -- 2027-06-01, 2026-09-14 -- and none is due."""
    assert parse_due(text, WEDNESDAY) is None


def test_a_past_clause_is_skipped_and_the_next_phrase_taken() -> None:
    due = parse_due("월요일에 회의 있었는데 금요일까지 하겠습니다", WEDNESDAY)

    assert due == DueDate(text="금요일", date=date(2026, 9, 11))


def test_a_deadline_word_makes_a_deadline_whatever_follows() -> None:
    due = parse_due("금요일까지 지난번에 말씀드렸던 거 드리겠습니다", WEDNESDAY)

    assert due == DueDate(text="금요일", date=date(2026, 9, 11))


@pytest.mark.parametrize(
    ("text", "due"),
    [
        ("월요일에 공유드릴게요", date(2026, 9, 14)),
        ("월요일에 보고드리겠습니다", date(2026, 9, 14)),
        ("월요일에 발표가 있으니 준비하겠습니다", date(2026, 9, 14)),
    ],
)
def test_the_future_and_being_are_not_the_past(text: str, due: date) -> None:
    """-겠- and 있다 end in ㅆ too."""
    assert parse_due(text, WEDNESDAY) == DueDate(text="월요일", date=due)


@pytest.mark.parametrize(
    "text",
    [
        "금요일에 하기로 했습니다",
        "금요일에 배포하기로 결정했습니다",
        "금요일에 드리는 걸로 했습니다",
        "금요일에 마무리하는 걸로 얘기했었죠",
        "금요일에 끝내도록 했습니다",
        "금요일에 하자고 했습니다",
        "금요일에 배포하기로 했고 제가 준비하겠습니다",
        "금요일까지 하기로 했습니다",
    ],
)
def test_a_past_verb_that_dates_an_agreement_keeps_the_deadline(text: str) -> None:
    """The 했 is when it was agreed; Friday is when it is due (mkkim68, #159)."""
    assert parse_due(text, WEDNESDAY) == DueDate(text="금요일", date=date(2026, 9, 11))


@pytest.mark.parametrize(
    "text",
    [
        "월요일에 말씀드렸던 걸로 정리하겠습니다",
        "월요일에 공유했던 거 하기로 했습니다",
    ],
)
def test_an_agreement_after_the_past_verb_does_not_rescue_the_date(text: str) -> None:
    """The date's own verb comes first and is past; the agreement is about
    something else."""
    assert parse_due(text, WEDNESDAY) is None


def test_a_day_long_past_named_without_a_deadline_word_gets_no_date() -> None:
    """The price of not inventing 2027-06-01: a real "3월 2일에" in September is
    missed. A missing date on a draft card, not a wrong one."""
    assert parse_due("3월 2일에 드리겠습니다", WEDNESDAY) is None


# --- the past adnominal, but only for a named few verbs (#197) ------------------


@pytest.mark.parametrize(
    "text",
    [
        "월요일에 말씀드린 거 정리하겠습니다",
        "월요일에 공유한 자료 다시 보내겠습니다",
        "월요일에 보낸 파일 기준으로 정리하겠습니다",
        "월요일에 전달한 내용 다시 확인하겠습니다",
    ],
)
def test_a_named_verbs_past_adnominal_is_now_read_as_the_past(text: str) -> None:
    """ "말씀드린" reads the same as "말씀드렸던" now -- #197's #2. Said on
    Wednesday 09-09, each would read as next Monday if taken forward."""
    assert parse_due(text, WEDNESDAY) is None


def test_an_adjectives_present_form_is_still_not_the_past() -> None:
    """ "필요한" is not one of the four verbs -- an adjective's present, not a
    verb's past, and this module still cannot tell the two apart in general
    (#197's own reason for naming only a few verbs rather than a syllable
    rule)."""
    assert parse_due("월요일에 필요한 걸 다시 정리하겠습니다", WEDNESDAY) == DueDate(
        text="월요일", date=date(2026, 9, 14)
    )


@pytest.mark.parametrize(
    "text",
    [
        "금요일까지 지난번에 말씀드린 거 드리겠습니다",  # deadline word overrides
    ],
)
def test_a_deadline_word_overrides_the_named_verbs_past_adnominal_too(text: str) -> None:
    assert parse_due(text, WEDNESDAY) == DueDate(text="금요일", date=date(2026, 9, 11))


def test_a_past_adnominal_with_no_agreement_marker_stays_the_past() -> None:
    """ "말씀드린 걸로" has no ``_AGREED`` match at all -- "걸로" needs a "는" or
    "할" right before it, and "린" is neither -- so this is plain past, the
    same shape as the existing "말씀드렸던 걸로" case."""
    assert parse_due("월요일에 말씀드린 걸로 정리하겠습니다", WEDNESDAY) is None


@pytest.mark.parametrize(
    "text",
    [
        "월요일에 자료 공유한다면 좋겠습니다",
        "월요일에 자료 전달한다면 좋겠습니다",
        "월요일에 자료 보낸다고 하셨어요",
        "월요일에 말씀드린다면 좋겠습니다",
    ],
)
def test_a_named_verbs_present_conditional_is_not_the_past(text: str) -> None:
    """Each of the four past-adnominal forms is also an exact prefix of the
    same verb's present/conditional -ㄴ다 conjugation -- "공유한" opens
    "공유한다면" the same way "겠" opens "겠다", which ``_NOT_PAST`` already
    excludes for the syllable check. Without the ``(?!다)`` lookahead these
    read as the past and lost their date the same way the genuine past forms
    do (review by lsh2217 on #333, reproduced against all four verbs)."""
    assert parse_due(text, WEDNESDAY) == DueDate(text="월요일", date=date(2026, 9, 14))
