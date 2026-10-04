"""A meeting summary written by a cloud LLM (``summary_impl=llm``, #421 v2).

The 요약 tab's v1 is B's own rows -- counts, decisions, items -- and no model.
This adds what a reader looks for first and v1 cannot give: a few sentences on
what the meeting was about. Opt-in and never the default, the same as every
other cloud implementation in this module, and refused at start-up unless the
deployment acknowledged #392 (``ExtractionSettings``).

**Hierarchical, because of the outbound limit.** ``check_outbound`` refuses a
body over ``MAX_OUTBOUND_CHARS``, and a meeting is longer than that. So the
lines are cut into sections that fit, each section is summarised into a few
points, and the points -- shorter than the lines they came from -- are what the
last call turns into the meeting's overview. When the points of a long meeting
still do not fit one call, they are summarised again the same way. A meeting
that fits one call is summarised in one.

**What leaves** is what ``LlmClassifier`` sends: masked utterance text only, no
speaker, no id, no meeting; only the consented speakers' lines (the task reads
them through ``service.summary_lines``); the team's names replaced by
``[사람N]``, numbered once across the whole meeting so a person is the same
number in every section, and put back in the answer. Every request goes
through ``HttpClient`` and its outbound check.

**What is kept from an answer** is checked without another model, the way the
resolver's answers are: one line per sentence, no placeholder the model
invented, and no number that is nowhere in the meeting. A point that fails is
dropped; an overview that fails drops the whole summary -- no summary is
better than a wrong first paragraph.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from autune_core import get_logger
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .llm import GeminiClient, _answer_text, substitute_names_mapped

log = get_logger(__name__)

MAX_CALLS = 12
"""Calls per meeting at most. A free-tier key allows a few hundred of the first
model's a day; a very long meeting must not spend them, and past this it gets
no summary rather than a partial one."""

MAX_POINTS = 7
MAX_POINT_CHARS = 200
MAX_OVERVIEW_CHARS = 400

_SECTION_PROMPT = """\
다음은 회의 녹취록의 한 부분입니다. 한 줄이 한 발화입니다. [사람N]은 가린 사람 이름입니다.

{lines}

이 부분에서 논의된 주제, 정한 것, 맡은 일, 남은 문제를 3~5개의 짧은 문장으로 정리하세요.
규칙:
- 녹취록에 없는 사실(날짜, 숫자, 이름)을 만들지 마세요.
- [사람N]과 대괄호로 묶인 마스킹 토큰은 그대로 두세요.
- 한 문장은 한 줄로 쓰세요.
JSON 하나만 출력하세요: {{"points": ["문장", ...]}}
"""

_FINAL_PROMPT = """\
다음은 회의 {source}입니다. 한 줄이 하나입니다. [사람N]은 가린 사람 이름입니다.

{lines}

회의 전체를 정리하세요. "overview"에는 회의가 무엇에 관한 것이었고 무엇이 정해졌는지 2~3문장, \
"points"에는 주요 내용 3~{max_points}개를 짧은 문장으로 쓰세요.
규칙:
- 위에 없는 사실(날짜, 숫자, 이름)을 만들지 마세요.
- [사람N]과 대괄호로 묶인 마스킹 토큰은 그대로 두세요.
- 한 문장은 한 줄로 쓰세요.
JSON 하나만 출력하세요: {{"overview": "...", "points": ["문장", ...]}}
"""

_PROMPT_OVERHEAD = max(len(_SECTION_PROMPT), len(_FINAL_PROMPT)) + 200
"""The prompt's own text plus the body's other strings; the lines get the rest."""
_LINE_BUDGET = MAX_OUTBOUND_CHARS - _PROMPT_OVERHEAD

_PLACEHOLDER = re.compile(r"\[사람\d+\]")
_DIGIT_RUN = re.compile(r"\d+")


class TooLongError(RuntimeError):
    """The meeting needs more than ``MAX_CALLS`` calls. Carries no text."""


@dataclass(frozen=True)
class WrittenSummary:
    overview: str
    points: tuple[str, ...]
    model_version: str


def sections(lines: Sequence[str], budget: int = _LINE_BUDGET) -> list[list[str]]:
    """``lines`` cut, in order, into runs whose rendered size fits ``budget``.

    A line longer than the budget on its own is left out rather than sent: the
    outbound check would refuse the call and with it the summary. The summary
    then never saw that one line, which costs it a little, not the meeting."""
    out: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        cost = len(line) + 3  # "- " and the newline
        if cost > budget:
            continue
        if current and size + cost > budget:
            out.append(current)
            current, size = [], 0
        current.append(line)
        size += cost
    if current:
        out.append(current)
    return out


def _render(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def _json(answer: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", answer, re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _kept(
    sentence: Any, surface: dict[str, str], said: str, *, limit: int = MAX_POINT_CHARS
) -> str | None:
    """One sentence of an answer, names put back -- or ``None`` when it should not
    be shown: not a string, blank, several lines, too long, a placeholder never
    sent, or a number the meeting never said."""
    if not isinstance(sentence, str):
        return None
    text = sentence.strip().strip("\"'“”").strip()
    if not text or "\n" in text or len(text) > limit:
        return None
    if any(marked not in surface for marked in _PLACEHOLDER.findall(text)):
        return None
    if any(number not in said for number in _DIGIT_RUN.findall(text)):
        return None
    return _PLACEHOLDER.sub(lambda m: surface[m.group(0)], text)


def _points(value: Any, surface: dict[str, str], said: str) -> list[str]:
    if not isinstance(value, list):
        return []
    kept = (_kept(sentence, surface, said) for sentence in value)
    return [p for p in kept if p is not None][:MAX_POINTS]


class LlmSummarizer(GeminiClient):
    """Gemini's ``generateContent`` over a meeting's masked lines, in sections."""

    def summarize(self, lines: Sequence[str]) -> WrittenSummary | None:
        """The meeting's overview and points, or ``None`` when there is nothing
        to summarise or the answer could not be used. ``TooLongError`` past
        ``MAX_CALLS``; a failed call and a privacy refusal are raised -- the
        task logs them by meeting id and the tab stays as v1 built it."""
        if not any(line.strip() for line in lines):
            return None
        scrubbed, surface = substitute_names_mapped(list(lines), self._roster)
        said = "\n".join(scrubbed)
        calls = 0
        current: list[str] = [line for line in scrubbed if line.strip()]
        source = "녹취록"
        while True:
            parts = sections(current)
            if len(parts) <= 1:
                break
            if calls + len(parts) + 1 > MAX_CALLS:
                raise TooLongError(f"needs more than {MAX_CALLS} calls")
            points: list[str] = []
            for part in parts:
                calls += 1
                answer = self._ask(_SECTION_PROMPT.format(lines=_render(part)), calls)
                # Placeholders stay placeholders between levels: only the last
                # answer is restored, so the next call never sees a name.
                points += _points(_json(answer).get("points"), {m: m for m in surface}, said)
            if not points:
                return None
            current, source = points, "부분별 요약"
        calls += 1
        prompt = _FINAL_PROMPT.format(source=source, lines=_render(current), max_points=MAX_POINTS)
        data = _json(self._ask(prompt, calls))
        overview = (
            _kept(data.get("overview"), surface, said, limit=MAX_OVERVIEW_CHARS) if data else None
        )
        if overview is None:
            log.info("extraction_summary_unusable", calls=calls)
            return None
        log.info("extraction_summary_written", calls=calls, lines=len(lines))
        return WrittenSummary(
            overview=overview,
            points=tuple(_points(data.get("points"), surface, said)),
            model_version=self.model_version,
        )

    def _ask(self, prompt: str, index: int) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        return _answer_text(self._post(body, index=index))
