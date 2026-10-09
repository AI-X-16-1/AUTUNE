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
        ("다음 주 말씀드리겠습니다", "다음 주", date(2026, 9, 18)),
        ("다음 주 초안 드릴게요", "다음 주", date(2026, 9, 18)),
        # Part of a week, by its last weekday: 초 Mon-Tue, 중반 Wed-Thu, 말 Fri.
        ("다음 주 초에 드릴게요", "다음 주 초", date(2026, 9, 15)),
        ("다음 주초까지 하겠습니다", "다음 주초", date(2026, 9, 15)),
        ("다음 주 초반에 공유할게요", "다음 주 초반", date(2026, 9, 15)),
        ("다음 주 중반까지 하겠습니다", "다음 주 중반", date(2026, 9, 17)),
        ("이번 주 중반까지 드릴게요", "이번 주 중반", date(2026, 9, 10)),
        ("다음 주 말까지 하겠습니다", "다음 주 말", date(2026, 9, 18)),
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


def test_early_this_week_said_on_a_friday_has_passed() -> None:
    """Early this week ended on Tuesday; said on Friday it is not a deadline."""
    assert parse_due("이번 주 초에 드릴게요", date(2026, 9, 11)) is None


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
    excludes for the syllable check. They are not followed by a noun that
    takes a past mention, so ``_PAST_ADNOMINAL_VERBS`` does not match (review
    by lsh2217 on #333, reproduced against all four verbs)."""
    assert parse_due(text, WEDNESDAY) == DueDate(text="월요일", date=date(2026, 9, 14))


@pytest.mark.parametrize(
    "text",
    [
        "월요일에 자료 공유한 후 피드백 주세요",
        "월요일에 자료 공유한 뒤에 회의하겠습니다",
        "월요일에 보낸 다음 확인하겠습니다",
        "월요일에 전달한 다음에 연락드릴게요",
        "월요일에 보낸대요",
        # Third review round on #333: each dated on main, lost here before
        # the lookahead became an allow-list.
        "월요일에 자료 공유한 이후에 피드백 주세요",
        "월요일에 보낸 직후 연락드릴게요",
        "월요일에 보낸답니다",
        "월요일에 자료 공유한답니다",
        "월요일에 보낸단다",
    ],
)
def test_a_named_verbs_relative_past_before_a_later_event_is_not_the_past(text: str) -> None:
    """-(으)ㄴ before "후/뒤/다음" is past relative to the event that follows
    it, not relative to the utterance -- "공유한 후 피드백 주세요" asks for
    feedback after a future Monday, the same shape as "3월 2일에
    드리겠습니다" in reverse. "보낸대요" is the same -ㄴ다 conjugation
    contracted to its colloquial quotative-present ("보낸다고 해" -> "보낸대")
    that ``test_a_named_verbs_present_conditional_is_not_the_past`` already
    covers for the full form, and "-ㄴ답니다/-ㄴ단다" its formal and plain
    forms. None is followed by a noun that takes a past mention, so each
    keeps its date exactly as on ``main`` (review by lsh2217 on #333, three
    rounds, reproduced against all four verbs)."""
    assert parse_due(text, WEDNESDAY) == DueDate(text="월요일", date=date(2026, 9, 14))


# --- a month, a half, a quarter or a year, by its last day (#197) ------------------


@pytest.mark.parametrize(
    ("text", "phrase", "due"),
    [
        ("10월 말까지 마무리하겠습니다", "10월 말", date(2026, 10, 31)),
        ("10월말에 드리겠습니다", "10월말", date(2026, 10, 31)),
        ("10월까지 하겠습니다", "10월", date(2026, 10, 31)),
        ("11월 중으로 정리하겠습니다", "11월", date(2026, 11, 30)),
        ("11월에 공유드릴게요", "11월", date(2026, 11, 30)),
        ("이번 달까지 끝내겠습니다", "이번 달", date(2026, 9, 30)),
        ("다음 달 중에 하겠습니다", "다음 달", date(2026, 10, 31)),
        ("다음 달에 보고드리겠습니다", "다음 달", date(2026, 10, 31)),
        ("연말까지 하겠습니다", "연말", date(2026, 12, 31)),
        ("올해 안에 배포하겠습니다", "올해 안", date(2026, 12, 31)),
        ("하반기에 출시하겠습니다", "하반기", date(2026, 12, 31)),
        ("2027년 상반기까지 이전하겠습니다", "2027년 상반기", date(2027, 6, 30)),
        ("내년 상반기 중에 하겠습니다", "내년 상반기", date(2027, 6, 30)),
        ("3분기 안에 끝내겠습니다", "3분기", date(2026, 9, 30)),
        ("내년 1분기까지 하겠습니다", "내년 1분기", date(2027, 3, 31)),
        # A month long past, with a deadline word, is next year's -- _next's rule.
        ("3월까지 드리겠습니다", "3월", date(2027, 3, 31)),
    ],
)
def test_a_period_is_due_by_its_last_day(text: str, phrase: str, due: date) -> None:
    """Said on Wednesday 2026-09-09."""
    assert parse_due(text, WEDNESDAY) == DueDate(text=phrase, date=due)


@pytest.mark.parametrize(
    "text",
    [
        # A month or a half with nothing after it that makes it a deadline.
        "5월 자료 기준으로 정리하겠습니다",
        "다음 달 일정은 제가 잡겠습니다",
        "하반기 실적 보고서 정리하겠습니다",
        # A month long past with 에 and no deadline word stays in the past.
        "5월에 나온 이슈 정리하겠습니다",
        "3월에 드리겠습니다",
        # 말 that is 말씀.
        "10월 말씀드린 건 제가 하겠습니다",
        "올해 말씀드린 대로 하겠습니다",
        # This month, said of the past.
        "9월에 공유드렸는데 다시 보내겠습니다",
        "9월에 공유한 자료 다시 보내겠습니다",
        "상반기에 했던 거 다시 하겠습니다",
        # A count of months is not a month.
        "3개월 뒤에 다시 보죠",
    ],
)
def test_a_period_that_is_not_a_deadline_has_no_date(text: str) -> None:
    assert parse_due(text, WEDNESDAY) is None


def test_a_named_year_needs_no_meeting_day_and_a_relative_period_does() -> None:
    assert parse_due("2027년 상반기까지 이전하겠습니다", None) == DueDate(
        text="2027년 상반기", date=date(2027, 6, 30)
    )
    assert parse_due("하반기에 출시하겠습니다", None) == DueDate(text="하반기", date=None)
    assert parse_due("내년 1분기까지 하겠습니다", None) == DueDate(text="내년 1분기", date=None)


def test_a_longer_phrase_still_wins_over_its_month() -> None:
    """ "10월 3일" and "다음 달 말" are not read as the month's end."""
    assert parse_due("10월 3일까지 하겠습니다", WEDNESDAY) == DueDate(
        text="10월 3일", date=date(2026, 10, 3)
    )
    assert parse_due("다음 달 말까지 하겠습니다", WEDNESDAY) == DueDate(
        text="다음 달 말", date=date(2026, 10, 31)
    )


# --- a phrase that names a thing, not the deadline (#616) ---------------------------


def test_the_phrase_a_deadline_word_follows_wins_over_one_naming_a_thing() -> None:
    """Found on the real-account stack (10-01): "이번 주" names the minutes; the
    deadline is "다음 주 금요일까지". The first phrase made it due this Friday."""
    due = parse_due(
        "이번 주 회의록은 제가 다음 주 금요일까지 정리해서 공유하겠습니다", date(2026, 10, 1)
    )

    assert due is not None
    assert (due.text, due.date) == ("다음 주 금요일", date(2026, 10, 9))


def test_with_no_deadline_word_the_first_phrase_is_still_taken() -> None:
    """Pinned (#616): a line that only names a thing by a date keeps reading as
    due then -- for a person to correct, rather than losing the only date."""
    due = parse_due("이번 주 회의록 정리하겠습니다", date(2026, 10, 1))

    assert due is not None and due.date == date(2026, 10, 2)


def test_the_first_deadline_still_wins_over_what_happens_after() -> None:
    due = parse_due("다음 주 금요일까지 하고 월요일에 공유하겠습니다", date(2026, 10, 1))

    assert due is not None and due.date == date(2026, 10, 9)


# --- a date that is what a decision decided (2026-10-09) ------------------------

THURSDAY = date(2026, 10, 8)


@pytest.mark.parametrize(
    "text",
    [
        "배포 요일은 화요일로 바꾸기로 했습니다",
        "릴리스는 10월 20일로 정하기로 했습니다",
        "점검은 다음 주로 미루기로 했습니다",
        "정기 회의는 매주 월요일에 하기로 했습니다",
        "정산은 매달 15일에 하기로 했습니다",
        "회고는 격주 금요일에 하기로 했습니다",
        "회고는 금요일마다 하기로 했습니다",
    ],
)
def test_in_a_decision_a_date_chosen_or_repeating_is_not_a_deadline(text: str) -> None:
    assert parse_due(text, THURSDAY, decided=True) is None
    assert parse_due(text, THURSDAY) is not None, "a promise is still read as before"


@pytest.mark.parametrize(
    ("text", "due"),
    [
        ("릴리스는 10월 20일에 내기로 했습니다", date(2026, 10, 20)),
        ("견적서는 다음 주 금요일까지 받기로 했습니다", date(2026, 10, 16)),
        # A deadline word right after the date outranks the (으)로 behind it.
        ("마감은 금요일까지로 하기로 했습니다", date(2026, 10, 9)),
        # 로부터 is another word: the date is where something starts, as before.
        ("화요일로부터 일주일 안에 끝내기로 했습니다", date(2026, 10, 13)),
        # The date chosen is skipped and the deadline after it is found.
        ("회의는 화요일로 옮기고 자료는 금요일까지 내기로 했습니다", date(2026, 10, 9)),
    ],
)
def test_in_a_decision_a_date_something_is_due_by_is_still_found(text: str, due: date) -> None:
    found = parse_due(text, THURSDAY, decided=True)

    assert found is not None and found.date == due
