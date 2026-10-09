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
number in every section. Every request goes through ``HttpClient`` and its
outbound check.

**The summary names no person.** It says what was decided, what is to be done
by when, and what is left open -- not who. The lines go out without their
speakers, so the model cannot know who said "제가 할게요": measured on
2026-10-08 (three invented meetings) it gave a task to the wrong person in 2
of 15 names on the tab and 5 of 22 in the sections' answers. Telling it the
speaker would send something B sends to no model today, and a stored summary
shown to the team that says who proposed and who objected is one person's
stance on a decision (privacy.md, #168). Who took each task is already in the
item rows under the paragraph, where a person confirms it. So the prompts ask
for no person, and a sentence of the last answer that still carries a
``[사람N]`` or a speaker's own "제가" is dropped (``names_someone``). No name is
put back anywhere. The limit of the check: a name that is not on the team's
roster was never replaced, is no placeholder, and passes.

**What is kept from an answer** is checked without another model, the way the
resolver's answers are: one line per sentence, no person, no placeholder the
model invented, and no number that is nowhere in the meeting. A point that
fails is dropped; an overview that fails drops the whole summary -- no summary
is better than a wrong first paragraph -- except that an overview loses only
its sentences that name someone, and the summary only when none is left.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from autune_core import get_logger
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .llm import GeminiClient, _answer_text, substitute_names_mapped, unquoted
from .resolver import _numbers

log = get_logger(__name__)

MAX_CALLS = 12
"""Calls per meeting at most. A free-tier key allows a few hundred of the first
model's a day; a very long meeting must not spend them, and past this it gets
no summary rather than a partial one."""

MAX_POINTS = 7
LABELS = ("결정", "할 일", "남은 문제", "논의")
"""What a point of the last answer starts with, in the order the tab reads
them (``_FINAL_PROMPT`` asks for it, ``by_kind`` keeps to it)."""
MAX_POINT_CHARS = 200
MAX_OVERVIEW_CHARS = 400

_SECTION_PROMPT = """\
다음은 회의 녹취록의 한 부분입니다. 한 줄이 한 발화입니다. [사람N]은 가린 사람 이름이고, \
대괄호로 묶인 다른 표현은 가린 개인정보입니다.

{lines}

이 부분을 3~5개의 짧은 문장으로 정리하세요. 해당하는 것만, 이 순서로 쓰세요.
1. 정한 것: 무엇을 하기로 했는지
2. 맡은 일: 무엇을 언제까지 하기로 했는지
3. 남은 문제: 결론 없이 남은 질문이나 우려
4. 그 밖의 주요 논의
규칙:
- 녹취록에 없는 사실(날짜, 숫자, 이름)을 만들지 마세요. 기한은 녹취록의 표현 그대로 쓰세요.
- 사람을 쓰지 마세요. [사람N]도, 이름도, "제가"·"저는" 같은 말도 쓰지 않습니다. 누가 말했는지, \
누가 맡았는지, 누가 찬성하거나 반대했는지 없이 무엇을 하기로 했는지만 쓰세요.
- 그 밖의 대괄호 토큰은 그대로 두고, 새 대괄호나 괄호를 만들지 마세요.
- 맞장구, 인사, 잡담은 빼세요.
- 한 문장은 한 줄로 쓰고 "~습니다"로 끝내세요.
예시(다른 회의의 답):
{{"points": ["앱 출시는 다음 달 첫 주로 미루기로 했습니다.", \
"시안은 금요일까지 공유하기로 했습니다.", "결제 오류의 원인은 아직 모릅니다."]}}
JSON 하나만 출력하세요: {{"points": ["문장", ...]}}
"""

_FINAL_PROMPT = """\
다음은 회의 {source}입니다. 한 줄이 하나입니다. [사람N]은 가린 사람 이름이고, \
대괄호로 묶인 다른 표현은 가린 개인정보입니다.

{lines}
{board}
회의 전체를 정리하세요.
- "overview": 2~3문장. 첫 문장은 회의의 주제, 다음은 정해진 것과 남은 것입니다.
- "points": 주요 내용 3~{max_points}개. 각각 "결정: ", "할 일: ", "남은 문제: ", "논의: " 중 \
하나로 시작하고, 이 순서로 쓰세요. {max_points}개를 넘기지 마세요. 더 많으면 종류마다 중요한 \
것부터 골라, 한 종류가 다른 종류를 밀어내지 않게 하세요.
- "결정"은 하기로 정한 것입니다(미루거나 하지 않기로 정한 것도 결정입니다). "할 일"은 하기로 \
맡은 일(누가 맡았는지는 쓰지 않습니다), "남은 문제"는 결론이 나지 않은 것만입니다.
규칙:
- 위에 없는 사실(날짜, 숫자, 이름)을 만들지 마세요. 기한은 위의 표현 그대로 쓰세요.
- 사람을 쓰지 마세요. [사람N]도, 이름도, "제가"·"저는" 같은 말도 쓰지 않습니다. 누가 말했는지, \
누가 맡았는지, 누가 찬성하거나 반대했는지 없이 무엇을 하기로 했는지만 쓰세요.
- 그 밖의 대괄호 토큰은 그대로 두고, 새 대괄호나 괄호를 만들지 마세요.
- 같은 내용을 두 번 쓰지 말고, 맞장구, 인사, 잡담은 빼세요.
- 한 문장은 한 줄로 쓰고 "~습니다"로 끝내세요.
예시(다른 회의의 답):
{{"overview": "온보딩 화면 개편을 논의한 회의입니다. 첫 화면을 세 단계로 줄이기로 했고, \
문구 검토는 다음 회의로 넘겼습니다.", \
"points": ["결정: 온보딩 첫 화면을 세 단계로 줄이기로 했습니다.", \
"할 일: 시안은 다음 주까지 공유하기로 했습니다.", \
"남은 문제: 문구를 누가 검토할지는 정하지 않았습니다."]}}
JSON 하나만 출력하세요: {{"overview": "...", "points": ["...", ...]}}
"""
"""What changed from the first draft, unmeasured as that draft was (no key in
the session that wrote either): a fixed order and a label on each point, so
the tab reads 결정 / 할 일 / 남은 문제 the way the board below it does; one
register ("~습니다"); a worked example from another meeting, with no
placeholder in it -- a ``[사람1]`` copied from an example would cost the
sentence it is in; and, when there is one, the board the run already built
(``_BOARD_HEADER``), so the paragraph cannot contradict the rows under it.

Measured for the first time on 2026-10-08 (three invented meetings, five
summaries): given the board the model wrote 13, 8 and 10 points where 3 to 7
were asked for, decisions first, and three points sat under the wrong label.
So the limit is now said twice, with what to do when there is more, and each
label says what it is for. What the answer still gets wrong is ``by_kind``'s
to bound. And both prompts now ask for no person, in the rule and in the
examples, which named a role (the module docstring says why).

Measured again the same day, the model kept to the limit (7, 6 and 7 points)
and two of the three summaries were seven decisions and nothing else: with a
board of many decisions it spends every point on them, and an open question
is then in the overview or nowhere. That is a known limit, and one fix for it
was tried and dropped: a share per kind in this prompt ("decisions at most
four, every open question listed") did change the kinds (4/1/2 in the long
meeting), and made the model call three things the meeting had decided
undecided, each against a board row it was given. A summary that lists only
decisions is true; that one was not."""

_BOARD_HEADER = (
    '\n아래는 이 회의에서 뽑아 둔 결정과 할 일입니다. "확인 전"은 사람이 아직 확인하지 '
    "않은 것이니 위 내용과 맞을 때만 반영하세요. 요약은 이 목록과 어긋나지 않게 쓰세요.\n"
)
MAX_BOARD_CHARS = 1000
"""What the board may take of the last call: a long board must not crowd out
the lines it summarises. Past this its last rows are left out."""

_PROMPT_OVERHEAD = max(len(_SECTION_PROMPT), len(_FINAL_PROMPT)) + 200
"""The prompt's own text plus the body's other strings; the lines get the rest
(and the last call gives the board its share of that, ``summarize``)."""
_LINE_BUDGET = MAX_OUTBOUND_CHARS - _PROMPT_OVERHEAD

_PLACEHOLDER = re.compile(r"\[사람\d+\]")
_FIRST_PERSON = re.compile(r"(?<![가-힣])(?:제가|저는|저도|제게|내가)(?![가-힣])")
"""A speaker's word for themselves, as its own word: not the 제가 of 문제가."""
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


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


def _board(rows: Sequence[str]) -> str:
    """The board block of the last prompt, or "" with no rows. Rows past
    ``MAX_BOARD_CHARS`` are left out from the end."""
    kept: list[str] = []
    size = len(_BOARD_HEADER)
    for row in rows:
        if not row.strip() or "\n" in row:
            continue
        if size + len(row) + 3 > len(_BOARD_HEADER) + MAX_BOARD_CHARS:
            break
        kept.append(row)
        size += len(row) + 3
    return _BOARD_HEADER + _render(kept) + "\n" if kept else ""


def _json(answer: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", answer, re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def names_someone(text: str) -> bool:
    """``text`` points at a person: a roster placeholder, or a speaker's own
    "제가" copied from a line -- which on the tab would mean nobody, or whoever
    is reading."""
    return bool(_PLACEHOLDER.search(text) or _FIRST_PERSON.search(text))


def _kept(
    sentence: Any,
    surface: dict[str, str],
    said: str,
    *,
    limit: int = MAX_POINT_CHARS,
    shown: bool = False,
) -> str | None:
    """One sentence of an answer -- or ``None`` when it should not be kept: not
    a string, blank, several lines, too long, a placeholder never sent, or a
    number the meeting never said.

    A number is compared whole (``_numbers``). Looked for as text it was
    found inside any longer one: "10월 2일" passed for a meeting that said
    "10월 20일" (2026-10-09, an answer written by hand).

    ``shown`` is a sentence of the last answer, the one a person reads: it is
    not kept when it names someone either. A section's point may keep its
    placeholder: it goes to the next call and is never stored."""
    if not isinstance(sentence, str):
        return None
    text = unquoted(sentence)
    if not text or "\n" in text or len(text) > limit:
        return None
    if shown and names_someone(text):
        return None
    if any(marked not in surface for marked in _PLACEHOLDER.findall(text)):
        return None
    if not _numbers(text) <= _numbers(said):
        return None
    return text


def _overview(value: Any, surface: dict[str, str], said: str) -> str | None:
    """The answer's overview without its sentences that name someone, or
    ``None`` when it fails as a whole or nothing of it is left."""
    text = _kept(value, surface, said, limit=MAX_OVERVIEW_CHARS)
    if text is None:
        return None
    return " ".join(s for s in _SENTENCE_END.split(text) if not names_someone(s)) or None


def _named(data: dict[str, Any]) -> int:
    """How many sentences of the last answer named someone. A count, for the log."""
    overview, points = data.get("overview"), data.get("points")
    sentences = _SENTENCE_END.split(overview) if isinstance(overview, str) else []
    sentences += [p for p in points if isinstance(p, str)] if isinstance(points, list) else []
    return sum(1 for s in sentences if names_someone(s))


def _kind(point: str) -> int:
    """Where ``point``'s label stands in ``LABELS``; past the end with none."""
    for n, label in enumerate(LABELS):
        if point.startswith(f"{label}:"):
            return n
    return len(LABELS)


def by_kind(points: Sequence[str], limit: int = MAX_POINTS) -> list[str]:
    """At most ``limit`` of ``points``: one of every kind before a second of
    any, and so on round, then in the order of ``LABELS``.

    The first ``limit`` were kept before, and the model writes decisions first
    and more points than it is asked for: of 13 (twelve decisions, one task)
    the tab showed six decisions and the task, of 10 it showed no open question
    at all (2026-10-08). A reader of the tab should see that there *was* an
    open question before a seventh decision; the decisions are all in the rows
    under the paragraph anyway. Within a kind the model's own order stands.

    A point with none of the labels is a kind of its own, last -- which is
    every point of a section's answer, so those are still the first ``limit``.
    """
    kinds: list[list[str]] = [[] for _ in range(len(LABELS) + 1)]
    for point in points:
        kinds[_kind(point)].append(point)
    taken = [0] * len(kinds)
    left = limit
    while left > 0 and any(taken[n] < len(kind) for n, kind in enumerate(kinds)):
        for n, kind in enumerate(kinds):
            if left > 0 and taken[n] < len(kind):
                taken[n] += 1
                left -= 1
    return [point for n, kind in enumerate(kinds) for point in kind[: taken[n]]]


def _points(value: Any, surface: dict[str, str], said: str, *, shown: bool = False) -> list[str]:
    if not isinstance(value, list):
        return []
    kept = (_kept(sentence, surface, said, shown=shown) for sentence in value)
    return by_kind([p for p in kept if p is not None])


class LlmSummarizer(GeminiClient):
    """Gemini's ``generateContent`` over a meeting's masked lines, in sections."""

    step = "summary"

    def summarize(
        self, lines: Sequence[str], *, board: Sequence[str] = ()
    ) -> WrittenSummary | None:
        """The meeting's overview and points, or ``None`` when there is nothing
        to summarise or the answer could not be used. ``TooLongError`` past
        ``MAX_CALLS``; a failed call and a privacy refusal are raised -- the
        task logs them by meeting id and the tab stays as v1 built it.

        ``board`` is the run's own decisions and items, one per line
        (``service.summary_board``), given to the last call only. Its names go
        through the same substitution as the lines, so a person is one number
        in both.

        A line too long for one call is in no call, the last one included
        (``sections``). With a board the last call has less room, so a line
        that would have fitted a section alone can be left out of a meeting
        short enough for one call."""
        if not any(line.strip() for line in lines):
            return None
        texts = list(lines)
        scrubbed_all, surface = substitute_names_mapped([*texts, *board], self._roster)
        scrubbed, scrubbed_board = scrubbed_all[: len(texts)], scrubbed_all[len(texts) :]
        said = "\n".join(scrubbed_all)
        board_text = _board(scrubbed_board)
        last_budget = _LINE_BUDGET - len(board_text)
        calls = 0
        current: list[str] = [line for line in scrubbed if line.strip()]
        source = "녹취록"
        while True:
            fitting = sections(current, last_budget)
            if len(fitting) <= 1:
                # What the last call sends is the run that fits, not ``current``:
                # ``sections`` leaves out a line too long for a call, and
                # rendering ``current`` would put it back -- past the outbound
                # limit, or out when it was meant to stay (#782 review).
                current = fitting[0] if fitting else []
                break
            parts = sections(current)
            if calls + len(parts) + 1 > MAX_CALLS:
                raise TooLongError(f"needs more than {MAX_CALLS} calls")
            points: list[str] = []
            for part in parts:
                calls += 1
                answer = self._ask(_SECTION_PROMPT.format(lines=_render(part)), calls)
                # Placeholders stay placeholders between levels, so the next
                # call never sees a name; the last answer may not carry one.
                points += _points(_json(answer).get("points"), surface, said)
            if not points:
                return None
            current, source = points, "부분별 요약"
        if not current:
            return None  # every line was too long for a call: nothing to send
        calls += 1
        prompt = _FINAL_PROMPT.format(
            source=source, lines=_render(current), board=board_text, max_points=MAX_POINTS
        )
        data = _json(self._ask(prompt, calls))
        overview = _overview(data.get("overview"), surface, said) if data else None
        named = _named(data)
        if overview is None:
            log.info("extraction_summary_unusable", calls=calls, named=named)
            return None
        log.info("extraction_summary_written", calls=calls, lines=len(lines), named=named)
        return WrittenSummary(
            overview=overview,
            points=tuple(_points(data.get("points"), surface, said, shown=True)),
            model_version=self.model_version,
        )

    def _ask(self, prompt: str, index: int) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        return _answer_text(self._post(body, index=index))
