"""Why the suggested date is that date, as one or two Korean sentences.

Spec section 7, Stage 2 (decided 2026-10-08, blanks 2026-10-08). **Dates and
titles are the rule's values; only the wording is a model's.** The model
writes a sentence with blanks -- ``{date}``, ``{due}``, ``{work}`` and the
rest of ``SLOTS`` -- and code fills them from ``rules.Why``. So the date the
card shows and the date in the sentence are one value, and the model can
neither choose nor move it.

The model reads ``facts(...)``: the step, the basis, which blanks it may and
must use, and counts. Never a date, a title, an owner or meeting text; the call
goes through ``GeminiText``, so ``check_outbound`` sees it.

A template is used only if ``usable`` passes it: one line, one or two Korean
sentences, every blank its step requires and none it does not offer, and
outside the blanks no date, weekday or day-pinning word and no number the
facts do not hold. Anything else -- no model configured, a failed or refused
call, a template that does not pass -- is the step's fixed template in
``FALLBACK``, filled the same way. The proposal never waits on or fails for
the model.

Written once, when Follow-up proposes; the card is not to call this again.
``Reason.template`` holds no meeting text (no title), so it is what a stored
row could keep, filled again from the row's own values when shown.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from autune_agent.main.gemini import gemini_text_from_settings

from . import rules

log = logging.getLogger(__name__)


class TextModel(Protocol):
    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str: ...


TEXT_FACTORY: Callable[[], TextModel] = gemini_text_from_settings
"""Replaced in tests. Raising from it means no model: the fixed template answers."""

SLOTS: dict[str, str] = {
    "date": "추천한 후속 회의 날짜 (예: 'M/D(요일)')",
    "due": "할 일들이 거의 다 끝나는 기한 날짜 (예: 'M/D')",
    "work": "그 기한까지 끝내기로 한 할 일 (예: \"'제목' 등 할 일 N건\" 또는 '할 일 N건')",
    "count": "그 기한까지의 할 일 건수 (예: 'N건')",
    "interval_days": "팀의 평소 회의 간격 (예: 'N일')",
    "last_meeting": "팀의 가장 최근 회의 날짜 (예: 'M/D(요일)')",
    "holiday_shift": "주말이나 공휴일을 피해 날짜를 옮긴 이유 (예: '공휴일(M/D)을 피해')",
    "overdue_count": "기한이 이미 지난 확정 할 일 건수 (예: 'N건')",
    "default_days": "기본으로 띄우는 영업일 수 (예: 'N영업일')",
}
"""Every blank, with what fills it. A blank's value carries its own unit."""

MAX_TITLE_CHARS = 20


@dataclass(frozen=True)
class Reason:
    template: str
    """With blanks; no date, no title. What a stored row could keep."""
    text: str
    """Filled: what the lead reads."""
    by_model: bool


def _blanks(suggestion: rules.Suggestion) -> tuple[frozenset[str], frozenset[str]]:
    """The blanks a step's sentence must use, and those it may."""
    why = _why(suggestion)
    shift = {"holiday_shift"} if why.moved_off_day and why.skipped else set()
    if why.step == "overdue":
        must, may = {"date"}, {"overdue_count"}
    elif why.step == "due_share":
        must, may = {"date", "work", "due"} | shift, {"count"}
    elif why.step == "cadence":
        must, may = {"date", "interval_days"} | shift, {"last_meeting"}
    else:
        must, may = {"date"}, {"default_days"}
    return frozenset(must), frozenset(must | may)


def _why(suggestion: rules.Suggestion) -> rules.Why:
    return suggestion.why or rules.Why(step="default")


FALLBACK: dict[str, str] = {
    "overdue": (
        "확정된 할 일 중 기한이 이미 지난 항목이 {overdue_count} 있어서, "
        "가장 빠른 영업일인 {date}에 후속 회의를 제안했습니다."
    ),
    "due_share": (
        "{work}을 {due}까지 완료하기로 해서, 결과를 함께 확인할 수 있도록 "
        "다음 영업일인 {date}에 후속 회의를 제안했습니다."
    ),
    "due_share_shifted": (
        "{work}을 {due}까지 완료하기로 해서, 결과를 함께 확인할 수 있도록 "
        "{holiday_shift} {date}에 후속 회의를 제안했습니다."
    ),
    "cadence": (
        "최근 회의가 {interval_days} 간격으로 열려서, 같은 간격에 맞춰 "
        "{date}에 후속 회의를 제안했습니다."
    ),
    "cadence_shifted": (
        "최근 회의가 {interval_days} 간격으로 열려서, 같은 간격에 맞추되 "
        "{holiday_shift} {date}에 후속 회의를 제안했습니다."
    ),
    "cadence_late": (
        "최근 회의가 {interval_days} 간격으로 열렸지만 같은 간격이면 이미 지난 날짜라서, "
        "가장 빠른 영업일인 {date}에 후속 회의를 제안했습니다."
    ),
    "default": (
        "아직 회의 기록이 적어 팀의 회의 간격을 알 수 없어서, "
        "{default_days} 뒤인 {date}에 후속 회의를 제안했습니다."
    ),
}
"""The fixed template of each step, and of a step whose day moved or was held."""


def fallback_key(suggestion: rules.Suggestion) -> str:
    why = _why(suggestion)
    if why.step == "cadence" and why.held_to_earliest:
        return "cadence_late"
    if why.step in ("due_share", "cadence") and "holiday_shift" in _blanks(suggestion)[0]:
        return f"{why.step}_shifted"
    return why.step


INSTRUCTIONS = (
    """\
당신은 후속 회의 추천 날짜가 왜 그 날짜인지 팀장에게 설명하는 문장의 틀을 씁니다.
날짜는 이미 규칙이 정했습니다. 당신은 날짜도 할 일 제목도 모릅니다.
그 자리에는 빈칸을 쓰고, 빈칸은 나중에 프로그램이 실제 값으로 채웁니다.

- 한국어 1~2문장, 존댓말("~제안했습니다")로, 한 줄로 씁니다.
- 빈칸은 {이름} 형태로, 입력의 allowed에 있는 이름만 씁니다.
  required의 빈칸은 모두 한 번씩 꼭 씁니다.
- 빈칸 값에는 단위가 이미 붙어 있습니다. "{interval_days} 간격"처럼 씁니다
  ("{interval_days}일"이라고 쓰지 않습니다).
- 빈칸 밖에는 날짜, 요일, 월, "다음 주"나 "내일" 같은 날을 가리키는 말을 쓰지 않습니다.
- 빈칸 밖에 숫자를 쓰려면 numbers에 있는 값만 씁니다. 되도록 빈칸을 씁니다.
- 입력에 있는 근거만 씁니다. 입력에 없는 이유, 추측, 권유는 쓰지 않습니다.
- 사람 이름이나 회의 내용은 쓰지 않습니다.

입력:
- step: overdue(확정된 할 일 중 기한이 지난 것이 있어 가장 빠른 영업일),
  due_share(할 일 기한의 대부분이 지난 직후 영업일),
  cadence(팀의 평소 회의 간격), default(회의 기록이 적어 기본 영업일 수)
- basis: confirmed(확정 기한만), draft(초안 기한 포함), cadence(회의 주기)
- moved_off_day: 주말이나 공휴일이라 날짜를 옮김. held_to_earliest: 주기대로면 이미 지난 날이라
  가장 빠른 영업일로 잡음.
- slots: 빈칸 이름과 그 뜻. allowed, required: 쓸 수 있는 빈칸과 꼭 써야 하는 빈칸.

좋은 예 (cadence): """
    + FALLBACK["cadence"]
    + """
좋은 예 (due_share): """
    + FALLBACK["due_share"]
    + """

문장의 틀만 출력합니다.
"""
)

MAX_CHARS = 200
_HANGUL = re.compile(r"[가-힣]")
_NUMBER = re.compile(r"\d+")
_BLANK = re.compile(r"\{([a-z_]+)\}")
_SENTENCE_END = re.compile(r"[.!?。](?:\s|$)")
_DATE_LIKE = re.compile(
    r"\d+\s*월|\d+\s*[/.-]\s*\d+|\d{4}|요일|\([월화수목금토일]\)|"
    r"오늘|내일|모레|어제|이번\s*주|다음\s*주|지난\s*주|다다음"
)
"""A date, a weekday, or a word that pins a day, outside the blanks."""


def facts(suggestion: rules.Suggestion) -> dict[str, object]:
    """What the model reads: the step, the basis, the blanks and counts. Never a
    date, never a title."""
    why = _why(suggestion)
    must, may = _blanks(suggestion)
    numbers: dict[str, int] = {}
    if why.step == "overdue":
        numbers["overdue_items"] = why.overdue
    elif why.step == "due_share":
        numbers |= {
            "items_due_by_point": why.covered,
            "dated_items": len(why.used),
            "items_beyond_horizon": why.beyond_horizon,
        }
    elif why.step == "cadence":
        numbers |= {"interval_days": why.cadence_days or 0, "meetings_read": why.meetings}
    else:
        numbers |= {
            "meetings_read": why.meetings,
            "default_business_days": rules.DEFAULT_BUSINESS_DAYS,
        }
    return {
        "step": why.step,
        "basis": suggestion.basis,
        "moved_off_day": "holiday_shift" in must,
        "held_to_earliest": why.held_to_earliest,
        "slots": {name: SLOTS[name] for name in sorted(may)},
        "allowed": sorted(may),
        "required": sorted(must),
        "numbers": numbers,
    }


def usable(template: str, suggestion: rules.Suggestion) -> bool:
    """Every rule the model's template must keep (module docstring)."""
    if not template or len(template) > MAX_CHARS or "\n" in template:
        return False
    must, may = _blanks(suggestion)
    used = _BLANK.findall(template)
    if not must <= set(used) or not set(used) <= may or len(used) != len(set(used)):
        return False
    outside = _BLANK.sub(" ", template)
    if "{" in outside or "}" in outside:
        return False
    if not _HANGUL.search(outside) or _DATE_LIKE.search(outside):
        return False
    if not 1 <= len(_SENTENCE_END.findall(template)) <= 2:
        return False
    given = facts(suggestion)["numbers"]
    allowed = {str(v) for v in given.values()} if isinstance(given, dict) else set()
    return all(n in allowed for n in _NUMBER.findall(outside))


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _mdw(day: date) -> str:
    return f"{_md(day)}({'월화수목금토일'[day.weekday()]})"


def _title(raw: str) -> str:
    title = " ".join(raw.replace("'", "").split())
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return f"'{title}'"


def _shift(why: rules.Why) -> str:
    """Why the day moved, by the day it would have been: a weekday passed
    over is a public holiday, and the weekend after it is no news."""
    first = why.skipped[0] if why.skipped else None
    if first is not None and first.weekday() < 5:
        return f"공휴일({_md(first)})을 피해"
    return "주말을 피해"


def values(suggestion: rules.Suggestion) -> dict[str, str]:
    """What fills each blank, from the rule's own values."""
    why = _why(suggestion)
    out = {
        "date": _mdw(suggestion.day),
        "overdue_count": f"{why.overdue}건",
        "count": f"{why.covered}건",
        "interval_days": f"{why.cadence_days or 0}일",
        "default_days": f"{rules.DEFAULT_BUSINESS_DAYS}영업일",
        "holiday_shift": _shift(why),
    }
    if why.share_point is not None:
        out["due"] = _md(why.share_point)
    if why.last_meeting is not None:
        out["last_meeting"] = _mdw(why.last_meeting)
    if why.title and why.covered > 1:
        out["work"] = f"{_title(why.title)} 등 할 일 {why.covered}건"
    elif why.title:
        out["work"] = f"{_title(why.title)} 1건"
    else:
        out["work"] = f"할 일 {why.covered}건"
    return out


def fill(template: str, suggestion: rules.Suggestion) -> str:
    filled = values(suggestion)
    return _BLANK.sub(lambda m: filled.get(m.group(1), m.group(0)), template)


def explain(suggestion: rules.Suggestion) -> Reason:
    """The sentence for the card: the model's template when usable, else the
    step's own, filled from the rule's values either way."""
    fallback = FALLBACK[fallback_key(suggestion)]
    try:
        answer = TEXT_FACTORY().generate(
            INSTRUCTIONS, json.dumps(facts(suggestion), ensure_ascii=False), json_answer=False
        )
    except Exception as exc:  # noqa: BLE001 - any failure is the fixed template
        # The type only: a refusal's message could echo what was sent.
        log.info("followup_reason_fallback", extra={"error": type(exc).__name__})
        return Reason(fallback, fill(fallback, suggestion), by_model=False)
    # A line break is a second paragraph, not part of a sentence: refused
    # before spaces are evened out.
    template = answer.strip()
    if "\n" in template or not usable(" ".join(template.split()), suggestion):
        log.info("followup_reason_fallback", extra={"error": "unusable"})
        return Reason(fallback, fill(fallback, suggestion), by_model=False)
    template = " ".join(template.split())
    return Reason(template, fill(template, suggestion), by_model=True)
