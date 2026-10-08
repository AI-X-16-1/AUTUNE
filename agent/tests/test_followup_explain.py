"""Follow-up's reason sentence (spec section 7, Stage 2): the rule's values in,
one or two sentences out, the step's fixed wording when the model's is unusable."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from autune_agent.subagents.followup import explain, rules

WEDNESDAY = date(2026, 10, 7)


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


def due(day: str, confirmed: bool = True) -> rules.Due:
    return rules.Due(date.fromisoformat(day), confirmed)


def by_due_dates() -> rules.Suggestion:
    suggestion = rules.suggest_from_due_dates(
        [due("2026-10-08"), due("2026-10-09"), due("2026-10-12"), due("2026-11-30")], WEDNESDAY
    )
    assert suggestion is not None
    return suggestion


# -- what the rule hands over ------------------------------------------------


def test_the_due_date_step_says_what_it_counted() -> None:
    why = by_due_dates().why

    assert why is not None
    assert why.step == "due_share"
    assert why.used == (date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12))
    assert (why.covered, why.share_point) == (3, date(2026, 10, 12))
    assert why.beyond_horizon == 1


def test_an_overdue_item_is_counted() -> None:
    suggestion = rules.suggest_from_due_dates([due("2026-10-01"), due("2026-10-02")], WEDNESDAY)

    assert suggestion is not None and suggestion.why is not None
    assert (suggestion.why.step, suggestion.why.overdue) == ("overdue", 2)


def test_the_rhythm_says_its_gap_and_last_meeting() -> None:
    held = [date(2026, 9, 23), date(2026, 9, 30), date(2026, 10, 7)]

    suggestion = rules.suggest_by_rhythm(held, WEDNESDAY)

    assert suggestion.day == rules.suggest_date(held, WEDNESDAY) == date(2026, 10, 14)
    assert suggestion.why == rules.Why(
        step="cadence", meetings=3, last_meeting=date(2026, 10, 7), cadence_days=7
    )


def test_a_holiday_moves_the_day_and_says_so() -> None:
    held = [date(2026, 9, 23), date(2026, 9, 30)]
    # A weekly team last met on Wed 09-30, so the rhythm lands on Wed 10-07.
    off = frozenset({date(2026, 10, 7)})

    suggestion = rules.suggest_by_rhythm(held, date(2026, 10, 1), off)

    assert suggestion.day == date(2026, 10, 8)
    assert suggestion.why is not None and suggestion.why.moved_off_day


def test_too_few_meetings_is_the_default_step() -> None:
    suggestion = rules.suggest_by_rhythm([], WEDNESDAY)

    assert suggestion.why == rules.Why(step="default", meetings=0)


def test_why_does_not_change_which_suggestions_are_equal() -> None:
    assert by_due_dates() == rules.Suggestion(date(2026, 10, 13), "confirmed")


# -- what the model reads ----------------------------------------------------


def test_the_model_reads_no_date(monkeypatch: pytest.MonkeyPatch) -> None:
    model = use(monkeypatch, FakeText("확정된 할 일 3개 중 3개가 지난 직후로 잡았습니다."))

    explain.explain(by_due_dates())

    (sent,) = model.sent
    given: dict[str, Any] = json.loads(sent)
    assert given["step"] == "due_share"
    assert given["dated_items"] == 3
    assert "2026" not in sent
    assert not any(isinstance(v, str) and "-" in v for v in given.values())


# -- what the card gets -------------------------------------------------------


def test_a_usable_sentence_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    sentence = "이 회의 할 일 3개 중 3개의 기한이 지난 직후 영업일로 잡았습니다."
    use(monkeypatch, FakeText(sentence))

    assert explain.explain(by_due_dates()) == sentence


@pytest.mark.parametrize(
    "answer",
    [
        "10월 13일 화요일로 잡았습니다.",
        "할 일 기한이 지난 다음 주 화요일로 잡았습니다.",
        "기한이 2026-10-12인 항목 뒤로 잡았습니다.",
        "할 일 5개 중 4개가 끝난 뒤로 잡았습니다.",
        "Scheduled after most items are due.",
        "",
        "첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.",
        "기한이 지난 뒤로 잡았습니다.\n담당자에게 알려 주세요.",
    ],
    ids=["date", "week-word", "iso", "invented-number", "english", "empty", "three", "newline"],
)
def test_an_unusable_sentence_falls_back(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    use(monkeypatch, FakeText(answer))

    assert explain.explain(by_due_dates()) == explain.FALLBACK["due_share"]


def test_a_failed_call_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeText(error=RuntimeError("timeout")))

    assert explain.explain(rules.suggest_by_rhythm([], WEDNESDAY)) == explain.FALLBACK["default"]


def test_no_model_configured_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def unset() -> FakeText:
        raise RuntimeError("AUTUNE_AGENT_LLM_API_KEY is unset")

    monkeypatch.setattr(explain, "TEXT_FACTORY", unset)
    held = [date(2026, 9, 30), date(2026, 10, 7)]

    assert explain.explain(rules.suggest_by_rhythm(held, WEDNESDAY)) == explain.FALLBACK["cadence"]


def test_every_step_has_fixed_wording_with_no_date() -> None:
    for step, wording in explain.FALLBACK.items():
        assert explain.usable(wording, {"step": step}), step
