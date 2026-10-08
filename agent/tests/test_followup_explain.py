"""Follow-up's reason sentence (spec section 7, Stage 2): a model writes a
template with blanks from the rule's counts; code fills the blanks with the
rule's dates and title; the step's fixed template when the model's is unusable."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from autune_agent.subagents.followup import explain, rules

WEDNESDAY = date(2026, 10, 7)
HANGUL_DAY = frozenset({date(2026, 10, 9)})
"""한글날, Friday 2026-10-09."""


class FakeText:
    def __init__(self, answer: str = "", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.sent: list[str] = []

    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str:
        self.sent.append(text)
        if self.error is not None:
            raise self.error
        return self.answer


def use(monkeypatch: pytest.MonkeyPatch, model: FakeText) -> FakeText:
    monkeypatch.setattr(explain, "TEXT_FACTORY", lambda: model)
    return model


def due(day: str, confirmed: bool = True, title: str | None = None) -> rules.Due:
    return rules.Due(date.fromisoformat(day), confirmed, title)


def by_due_dates(title: bool = True) -> rules.Suggestion:
    """Four items due by Thursday 10-15 and one long after: Friday 10-16."""
    items = [
        due("2026-10-08", title="회의록 정리" if title else None),
        due("2026-10-12"),
        due("2026-10-14"),
        due("2026-10-15", title="API 연동" if title else None),
        due("2026-11-30", title="분기 보고"),
    ]
    suggestion = rules.suggest_from_due_dates(items, WEDNESDAY)
    assert suggestion is not None
    return suggestion


def by_rhythm(off: frozenset[date] = frozenset()) -> rules.Suggestion:
    held = [date(2026, 9, 25), date(2026, 10, 2)]
    return rules.suggest_by_rhythm(held, WEDNESDAY, off)


# -- what the rule hands over: the same day, and why ---------------------------


def test_titles_do_not_move_the_date() -> None:
    assert by_due_dates().day == by_due_dates(title=False).day == date(2026, 10, 16)


def test_the_due_date_step_names_the_last_titled_item_by_its_point() -> None:
    why = by_due_dates().why

    assert why is not None
    assert (why.step, why.covered, why.share_point) == ("due_share", 4, date(2026, 10, 15))
    assert why.title == "API 연동"
    assert why.beyond_horizon == 1


def test_a_drafts_title_is_never_used() -> None:
    suggestion = rules.suggest_from_due_dates(
        [due("2026-10-08", confirmed=False, title="초안 제목")], WEDNESDAY
    )

    assert suggestion is not None and suggestion.why is not None
    assert suggestion.why.title is None


def test_the_rhythm_records_a_holiday_it_moved_past() -> None:
    suggestion = by_rhythm(HANGUL_DAY)

    assert suggestion.day == date(2026, 10, 12)
    assert suggestion.why is not None
    assert suggestion.why.planned == date(2026, 10, 9)
    assert suggestion.why.skipped == (date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11))


# -- what the model reads ------------------------------------------------------


def test_the_model_reads_blanks_and_counts_never_a_date_or_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = use(monkeypatch, FakeText())

    explain.explain(by_due_dates())

    (sent,) = model.sent
    given: dict[str, Any] = json.loads(sent)
    assert given["step"] == "due_share"
    assert given["required"] == ["date", "due", "work"]
    assert given["numbers"]["items_due_by_point"] == 4
    assert "2026" not in sent and "10/" not in sent
    assert "API 연동" not in sent and "회의록" not in sent


# -- a usable template, filled by code ----------------------------------------


def test_the_models_template_is_filled_with_the_rules_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = (
        "{work}을 {due}까지 마치기로 했으니, "
        "결과를 함께 볼 수 있게 {date}에 후속 회의를 제안했습니다."
    )
    use(monkeypatch, FakeText(template))

    reason = explain.explain(by_due_dates())

    assert reason.by_model and reason.template == template
    assert reason.text == (
        "'API 연동' 등 할 일 4건을 10/15까지 마치기로 했으니, "
        "결과를 함께 볼 수 있게 10/16(금)에 후속 회의를 제안했습니다."
    )


def test_a_long_title_is_cut() -> None:
    suggestion = rules.suggest_from_due_dates(
        [due("2026-10-08", title="결제 모듈 외부 API 연동과 예외 처리 정리 및 문서화")], WEDNESDAY
    )
    assert suggestion is not None

    work = explain.values(suggestion)["work"]

    assert work == "'결제 모듈 외부 API 연동과 예외…' 1건"


# -- a template that breaks a rule falls back ----------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("{work}을 마치면 {date}에 후속 회의를 제안했습니다.", id="missing-blank"),
        pytest.param(
            "{work}을 {due}까지 마치기로 해서 {owner}님과 {date}에 보기로 했습니다.",
            id="unknown-blank",
        ),
        pytest.param(
            "{work}을 {due}까지 마치기로 해서 10월 16일, {date}에 제안했습니다.",
            id="date-outside",
        ),
        pytest.param(
            "{work}을 {due}까지 마치기로 해서 다음 주 {date}에 제안했습니다.",
            id="week-word-outside",
        ),
        pytest.param(
            "{work} 중 7건을 {due}까지 마치기로 해서 {date}에 제안했습니다.",
            id="number-not-given",
        ),
        pytest.param("{work} {due} {date}", id="no-korean-sentence"),
        pytest.param(
            "{work}을 {due}까지. 그래서. {date}에 제안했습니다. 끝입니다.", id="three-sentences"
        ),
        pytest.param("{work}을 {due}까지 마치기로 했습니다.\n{date}에 제안했습니다.", id="newline"),
        pytest.param(
            "{work}을 {due}까지 마치고 {date}에, 또 {date}에 제안했습니다.", id="blank-twice"
        ),
    ],
)
def test_a_template_that_breaks_a_rule_falls_back(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    use(monkeypatch, FakeText(answer))

    reason = explain.explain(by_due_dates())

    assert not reason.by_model
    assert reason.template == explain.FALLBACK["due_share"]


def test_a_failed_call_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeText(error=RuntimeError("timeout")))

    reason = explain.explain(by_rhythm())

    assert not reason.by_model
    assert reason.text == (
        "최근 회의가 7일 간격으로 열려서, 같은 간격에 맞춰 10/9(금)에 후속 회의를 제안했습니다."
    )


def test_a_moved_day_must_say_why(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeText("최근 회의가 {interval_days} 간격이라 {date}에 제안했습니다."))

    reason = explain.explain(by_rhythm(HANGUL_DAY))

    assert not reason.by_model
    assert reason.text == (
        "최근 회의가 7일 간격으로 열려서, 같은 간격에 맞추되 공휴일(10/9)을 피해 "
        "10/12(월)에 후속 회의를 제안했습니다."
    )


# -- the fixed templates read well on their own ---------------------------------


def test_the_fixed_templates_with_and_without_a_title() -> None:
    def fixed(suggestion: rules.Suggestion) -> str:
        return explain.fill(explain.FALLBACK[explain.fallback_key(suggestion)], suggestion)

    assert fixed(by_due_dates(title=False)) == (
        "할 일 4건을 10/15까지 완료하기로 해서, 결과를 함께 확인할 수 있도록 "
        "다음 영업일인 10/16(금)에 후속 회의를 제안했습니다."
    )
    overdue = rules.suggest_from_due_dates([due("2026-10-01")], WEDNESDAY)
    assert overdue is not None
    assert fixed(overdue) == (
        "확정된 할 일 중 기한이 이미 지난 항목이 1건 있어서, "
        "가장 빠른 영업일인 10/8(목)에 후속 회의를 제안했습니다."
    )
    assert fixed(rules.suggest_by_rhythm([], WEDNESDAY)) == (
        "아직 회의 기록이 적어 팀의 회의 간격을 알 수 없어서, "
        "3영업일 뒤인 10/12(월)에 후속 회의를 제안했습니다."
    )
    late = rules.suggest_by_rhythm([date(2026, 9, 1), date(2026, 9, 8)], WEDNESDAY)
    assert fixed(late) == (
        "최근 회의가 7일 간격으로 열렸지만 같은 간격이면 이미 지난 날짜라서, "
        "가장 빠른 영업일인 10/8(목)에 후속 회의를 제안했습니다."
    )


def test_every_fixed_template_passes_its_own_check() -> None:
    cases = [
        by_due_dates(),
        by_rhythm(),
        by_rhythm(HANGUL_DAY),
        rules.suggest_by_rhythm([], WEDNESDAY),
        rules.suggest_by_rhythm([date(2026, 9, 1), date(2026, 9, 8)], WEDNESDAY),
    ]
    for suggestion in cases:
        key = explain.fallback_key(suggestion)
        assert explain.usable(explain.FALLBACK[key], suggestion), key
