"""E's metric glossary, retrieved by ``explain_metric`` (spec section 5).

Passages are Markdown under this package, one per ``## <key> | <title>``
section. Their text is Korean -- user-facing response content, the exception
the spec names -- and every number in it is a ``{placeholder}`` filled from the
constants below, so the glossary cannot drift from the code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from importlib import resources

from autune_contracts import ACTION_PROGRESS_STALE_AFTER
from autune_intelligence import prediction, service

FILES = ("quality.md", "gaps.md", "alignment.md", "prediction.md", "actions.md", "reports.md")


@dataclass(frozen=True)
class Passage:
    key: str
    title: str
    text: str


def _span(value: timedelta) -> str:
    days = value.days
    if days and days % 7 == 0:
        return f"{days // 7}주"
    if days:
        return f"{days}일"
    return f"{int(value.total_seconds() // 60)}분"


def _cutoffs() -> str:
    return ", ".join(f"{grade} {cutoff:.1f} 이상" for cutoff, grade in service.GRADE_CUTOFFS)


_WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")

CONSTANTS: dict[str, str] = {
    **{f"weight.{k}": f"{v:.0%}" for k, v in service.WEIGHTS.items()},
    "grade.cutoffs": _cutoffs(),
    "quality.decision_cadence": f"{service.DECISION_CADENCE_MINUTES:g}분",
    "gap.high_ceiling": f"{service.HIGH_GAP_CEILING}건",
    "heatmap.min_meetings": f"{service.MIN_MEETINGS_PER_HEATMAP_CELL}건",
    "prediction.horizon": f"{prediction.MISALIGNMENT_HORIZON_DAYS}일",
    "prediction.min_meetings": f"{prediction.MIN_MEETINGS}건",
    "action.window": _span(service.ACTION_COMPLETION_WINDOW),
    "action.min_meetings": f"{service.ACTION_PROGRESS_MIN_MEETINGS}건",
    "action.stale_after": _span(ACTION_PROGRESS_STALE_AFTER),
    "weekly.default_weekday": _WEEKDAYS[service.WEEKLY_REPORT_DEFAULT_WEEKDAY],
    "weekly.default_hour": f"{service.WEEKLY_REPORT_DEFAULT_HOUR}시",
    "weekly.catch_up": _span(service.WEEKLY_REPORT_CATCH_UP),
    "weekly.active_within": _span(service.WEEKLY_REPORT_ACTIVE_WITHIN),
    "report.max_chars": f"{service.MEETING_REPORT_MAX_CHARS:,}자",
    "report.correction_window": _span(service.CORRECTION_SEND_WINDOW),
}

_PLACEHOLDER = re.compile(r"\{([a-z_]+\.[a-z_]+)\}")


def fill(text: str) -> str:
    """Replace each ``{area.name}`` with its constant; an unknown name is a KeyError."""
    return _PLACEHOLDER.sub(lambda m: CONSTANTS[m.group(1)], text)


def _parse(name: str) -> list[Passage]:
    text = resources.files(__package__).joinpath(name).read_text(encoding="utf-8")
    out: list[Passage] = []
    for block in text.replace("\r\n", "\n").split("\n## ")[1:]:
        head, _, body = block.partition("\n")
        key, _, title = head.partition(" | ")
        out.append(Passage(key=key.strip(), title=title.strip(), text=fill(body.strip())))
    return out


@cache
def passages() -> tuple[Passage, ...]:
    return tuple(p for name in FILES for p in _parse(name))
