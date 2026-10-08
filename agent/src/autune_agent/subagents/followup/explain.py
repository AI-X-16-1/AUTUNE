"""Why the suggested date is that date, in one or two Korean sentences.

Spec section 7, Stage 2 (decided 2026-10-08). **The date is the rule's**
(``rules.py``); a model writes only the sentence, never sees the date and
cannot change it. It reads ``facts(why)`` and nothing else: the rule's step and
the numbers it used, as counts and as days relative to today. No date, no
item's title, no owner and no meeting text leave Autune here, and the call
goes through ``GeminiText``, so ``check_outbound`` sees it.

A sentence is kept only if ``usable`` passes it: one or two Korean sentences,
no date, and no number the facts do not hold, so it cannot give a reason the
rule did not use. Anything else -- no model configured, a failed or refused
call, a sentence that does not pass -- gives the step's fixed wording in
``FALLBACK``. The proposal never waits on the model and never fails for it.

Written once, when Follow-up proposes; the card is not to call this again.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Protocol

from autune_agent.main.gemini import gemini_text_from_settings

from . import rules

log = logging.getLogger(__name__)


class TextModel(Protocol):
    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str: ...


TEXT_FACTORY: Callable[[], TextModel] = gemini_text_from_settings
"""Replaced in tests. Raising from it means no model: the fixed wording answers."""

FALLBACK: dict[rules.Step, str] = {
    "overdue": "확정된 할 일 중 기한이 이미 지난 항목이 있어, 가장 빠른 영업일로 잡았습니다.",
    "due_share": "이 회의 할 일 기한의 대부분이 지난 직후로 잡았습니다.",
    "cadence": "할 일 기한으로 정할 수 없어, 팀의 평소 회의 간격에 맞춰 잡았습니다.",
    "default": "회의 기록이 적어 회의 간격을 알 수 없어, 기본 간격으로 잡았습니다.",
}
"""The step's own wording, when no model sentence is usable."""

INSTRUCTIONS = """\
당신은 후속 회의 추천 날짜가 왜 그 날짜인지 팀장에게 설명하는 문장을 씁니다.
날짜는 이미 규칙이 정했습니다. 입력 JSON은 규칙이 그 날짜를 정할 때 쓴 근거입니다.

- 한국어 1~2문장, 존댓말("~했습니다")로 씁니다.
- 입력에 있는 근거만 씁니다. 입력에 없는 이유, 추측, 권유는 쓰지 않습니다.
- 날짜, 요일, 월, "다음 주"나 "내일" 같은 날을 가리키는 말은 쓰지 않습니다.
- 숫자는 입력에 있는 값만 그대로 씁니다.
- 사람, 회의 내용, 할 일 제목은 입력에 없으므로 언급하지 않습니다.

입력 필드:
- step: overdue(확정된 할 일 중 기한이 지난 것이 있어 가장 빠른 영업일),
  due_share(할 일 기한들의 share_percent% 지점 직후 영업일),
  cadence(팀의 평소 회의 간격), default(회의 기록이 적어 기본 영업일 수)
- basis: confirmed(확정 기한만), draft(초안 기한 포함), cadence(회의 주기)
- 그 밖의 필드는 이름 그대로의 개수나 일수입니다. 0이거나 false인 필드는 언급하지 않아도 됩니다.
- moved_off_day: 주말이나 공휴일이라 그다음 영업일로 옮김
- held_to_earliest: 규칙의 날이 너무 일러서 가장 빠른 영업일로 잡음

설명 문장만 출력합니다.
"""

MAX_CHARS = 160
_HANGUL = re.compile(r"[가-힣]")
_NUMBER = re.compile(r"\d+")
_SENTENCE_END = re.compile(r"[.!?。](?:\s|$)")
_DATE_LIKE = re.compile(
    r"\d+\s*월|\d+\s*[/.-]\s*\d+|\d{4}|요일|\([월화수목금토일]\)|"
    r"오늘|내일|모레|어제|이번\s*주|다음\s*주|지난\s*주|다다음"
)
"""A date, a weekday, or a word that pins a day. The card shows the date
itself; a sentence that names one could name a different one."""


def facts(suggestion: rules.Suggestion) -> dict[str, str | int | bool]:
    """What the model reads: the step, the basis and the counts and day spans
    the rule used. Never a date."""
    why = suggestion.why or rules.Why(step="cadence")
    out: dict[str, str | int | bool] = {"step": why.step, "basis": suggestion.basis}
    if why.step == "overdue":
        out["overdue_items"] = why.overdue
    elif why.step == "due_share":
        out |= {
            "share_percent": round(rules.DUE_DATE_SHARE * 100),
            "dated_items": len(why.used),
            "items_due_by_point": why.covered,
            "horizon_days": rules.DUE_HORIZON_DAYS,
            "items_beyond_horizon": why.beyond_horizon,
            "past_draft_items_dropped": why.past_drafts,
        }
    elif why.step == "cadence":
        out |= {"meetings_read": why.meetings, "cadence_days": why.cadence_days or 0}
    else:
        out |= {
            "meetings_read": why.meetings,
            "default_business_days": rules.DEFAULT_BUSINESS_DAYS,
        }
    out |= {"moved_off_day": why.moved_off_day, "held_to_earliest": why.held_to_earliest}
    return out


def usable(sentence: str, given: dict[str, str | int | bool]) -> bool:
    """One or two Korean sentences, no date, and only the numbers ``given`` holds."""
    if not sentence or len(sentence) > MAX_CHARS or "\n" in sentence:
        return False
    if not _HANGUL.search(sentence) or _DATE_LIKE.search(sentence):
        return False
    if len(_SENTENCE_END.findall(sentence)) > 2:
        return False
    numbers = {str(v) for v in given.values() if isinstance(v, int) and not isinstance(v, bool)}
    return all(n in numbers for n in _NUMBER.findall(sentence))


def explain(suggestion: rules.Suggestion) -> str:
    """The sentence for the card: the model's when usable, else the step's own."""
    given = facts(suggestion)
    fallback = FALLBACK[suggestion.why.step if suggestion.why else "cadence"]
    try:
        answer = TEXT_FACTORY().generate(
            INSTRUCTIONS, json.dumps(given, ensure_ascii=False), json_answer=False
        )
    except Exception as exc:  # noqa: BLE001 - any failure is the fixed wording
        # The type only: a refusal's message could echo what was sent.
        log.info("followup_reason_fallback", extra={"error": type(exc).__name__})
        return fallback
    # A line break is a second paragraph, not part of a sentence: refused
    # before spaces are evened out.
    sentence = answer.strip()
    if "\n" in sentence or not usable(" ".join(sentence.split()), given):
        log.info("followup_reason_fallback", extra={"error": "unusable"})
        return fallback
    return " ".join(sentence.split())
