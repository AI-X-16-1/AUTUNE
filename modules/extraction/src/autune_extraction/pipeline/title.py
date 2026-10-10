"""A short title for an action item or a decision, written by a cloud LLM
(``title_impl=llm``; module B's owner, 2026-10-09).

The card and the decision row show twenty characters. Until now that was the
stored sentence cut off with "…" (``apps/web/.../actions/title.ts``); what was
asked for is a summary instead: twenty characters or fewer, ended by a noun
("보고서 정리", not "보고서를 정리함"), saying what is to be done or what was
settled and nothing else.

**Stored beside the sentence; it leads the copies that leave.** The description
and the statement stay as they are made today -- the said words tidied by rule
(``noun_form``), with the resolver's checks on a rewrite -- and stay what module
D, module E and the agent's tools are given. The title is one more column on
B's own rows. B's screens read it, and since 2026-10-09 so do the copies B
sends: Jira, Notion, a calendar, a line of a Slack message and of a project's
minutes lead with it where a row has one (``top_line``), the sentence going to
the copy's body where it has a body. So ``accept`` decides what leaves, not
only what a screen shows.

**A title is accepted or it is not there.** ``accept`` is rules, no model: one
line, twenty characters with the spaces, a noun at the end, no number that is
not in the sentence, no person, no date on an item, no word that only points
("이거 진행" names nothing; module B's owner, 2026-10-09), and words that are
the sentence's, all but one (``_own_words``: one word may be new, and each other
word has to begin as some part of the sentence does). No masker reads the
answer: the model was given the meeting's masked sentences and nothing else
of the meeting, and the outbound check reads the title wherever it is sent.
A title that fails any of them is dropped and the row has no
title -- the screen then shows what it showed before, the sentence cut. So a
refused title, a failed call and ``title_impl=none`` all look like yesterday.
Nothing is shortened by rule here: cutting an answer to fit would make a
title nobody wrote.

**No date and no owner on an item's title.** Both have fields of their own and
the card shows them; a title that repeats a date is one more place for a model
to get it wrong. A decision is different: "배포 금요일로 연기" is a date *being*
what was settled. There a date is allowed when it is in the sentence in the
same words, and the bracket the statement ends with -- "(담당 민경, 기한
2026-10-13)" -- is taken off before the sentence is sent (``decisions.core_of``).

**What leaves** is what the resolver sends: masked text of consenting speakers
only (the rows are built from nothing else), the team's names replaced by
``[사람N]``, no id, no speaker, no meeting. Every request goes through
``HttpClient`` and its outbound check. Opt-in like every cloud setting here,
and refused at start-up unless the deployment acknowledged #392.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from autune_core import get_logger
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .llm import (
    GeminiClient,
    _answer_text,
    says_a_pointing_word,
    substitute_names_mapped,
    unquoted,
)
from .resolver import _numbers

log = get_logger(__name__)

TITLE_MAX = 20
"""Characters, every one counted: a letter, a space, a digit, a mark. The
number the screen's own cut uses (``title.ts``)."""

MAX_PER_CALL = 20
"""Sentences in one request. A meeting has a dozen rows or so, so a meeting is
one call; the outbound limit cuts a batch sooner when the sentences are long."""

Kind = Literal["item", "decision"]


@dataclass(frozen=True)
class TitleRequest:
    """One row's sentence as it is stored -- a decision's without its bracket."""

    text: str
    kind: Kind = "item"


@dataclass(frozen=True)
class Title:
    """An accepted title, or ``None`` and the reason it was not accepted.

    ``answer`` is what the model wrote, for the evaluation's table. Nothing in
    the worker logs or stores it: it is meeting content, like the title."""

    text: str | None
    reason: str = ""
    answer: str = ""


_PROMPT = """\
다음은 회의에서 나온 할 일과 결정입니다. 항목마다, 무엇을 하는지(또는 무엇이 정해졌는지)만 \
남긴 짧은 제목을 쓰세요.

규칙:
- 공백을 포함해 20자 이내, 한 줄로 쓰세요.
- 명사로 끝내세요. "~함", "~됨", "~예정", "~합니다", "~해요", "~하기로"로 끝내지 마세요. \
[결정]은 "결정", "확정"이라는 말로 끝내지 않아도 됩니다.
- 항목에 있는 말만 쓰세요. 항목에 없는 숫자, 이름, 대상을 만들지 마세요.
- [할 일]에는 날짜, 요일, 기한, 담당자를 쓰지 마세요.
- [결정]은 정해진 내용이 날짜 그 자체일 때만, 항목에 적힌 날짜 표현을 그대로 쓰세요.
- 사람 이름과 대괄호로 묶인 표현은 쓰지 마세요. 괄호도 쓰지 마세요.
- 20자에 다 담을 수 없으면 가장 중요한 대상과 행동만 남기세요.

예시
[할 일] 다음 주 화요일까지 결제 화면 오류 로그를 모아서 정리 예정 -> 결제 화면 오류 로그 정리
[할 일] 금요일까지 보고서 정리 예정 -> 보고서 정리
[결정] 배포는 다음 주 금요일로 미루기로 함 -> 배포 다음 주 금요일로 연기
[결정] 테스트 서버 비용은 이번 달부터 절반으로 줄이기로 함 -> 테스트 서버 비용 절반 축소

항목:
{items}

JSON 하나만 출력하세요: {{"titles": [{{"n": 1, "title": "..."}}, ...]}}
"""

_LABEL = {"item": "[할 일]", "decision": "[결정]"}

_PROMPT_BUDGET = MAX_OUTBOUND_CHARS - 200

_VERB_END = re.compile(
    r"(?:니다|니까|어요|아요|해요|세요|예요|에요|네요|게요|까요|죠|한다|된다|했다|됐다|"
    r"있다|없다|이다|는다|기로|도록|하고|해서|하여)$"
)
"""An ending a sentence stops on, not a noun. Whole endings, not single
letters: "개요", "수요" and "필요" end in 요 and are nouns."""

_HAM_NOUNS = ("포함", "명함", "보관함", "우편함", "사서함", "수납함", "건의함", "투표함", "수거함")
"""Nouns that end in 함. Every other word ending in 함 or 됨 here is the
nominal ending that was asked not to be written ("정리함", "확정됨")."""

_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d+\s*월|\d+\s*일|\d+\s*시(?!간)|\d+\s*분기|[월화수목금토일]요일|"
    r"(?:다음|이번|지난|저번|오는)\s*(?:주|달|분기|해)|오늘|내일|모레|어제|주말|월말|연말|"
    r"올해|내년|작년|오전|오후"
)
"""A point in time, as ``title.ts`` reads one. "매주" and "분기별" are not
here: how often is part of what is to be done, not when it is due. Nor is
"2시간": how long, not when."""

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _last_word(title: str) -> str:
    words = _WORD.findall(title)
    return words[-1] if words else ""


def _ends_in_a_noun(title: str, kind: Kind) -> bool:
    last = _last_word(title)
    if not last or _VERB_END.search(last):
        return False
    if last.endswith("됨") or (last.endswith("함") and not last.endswith(_HAM_NOUNS)):
        return False
    if last == "예정":
        return False
    # "A안으로 결정" on the decisions tab says twice that it is a decision. An
    # item may be to decide something: "요금제 결정" is what is to be done.
    return not (kind == "decision" and last in ("결정", "확정"))


def _own_words(title: str, text: str) -> bool:
    """Whether the title is made of the sentence's words: every word but one
    starts as a word of the sentence does.

    The start, because the end is where a title differs on purpose ("정리해"
    said, "정리" written). One word may be new, because the noun a title ends
    on is often not in the sentence at all ("미루기로" said, "연기" written).
    Two new words is a title about something else. Unmeasured, like the
    resolver's ``_TARGET_ENDING_LENGTH``: picked as the smallest rule that can
    be read, and what the first real run is to say is how often it refuses."""
    said = text.replace(" ", "")
    words = _WORD.findall(title)
    new = sum(1 for word in words if word[:2] not in said)
    return new <= 1


def accept(
    answer: str, request: TitleRequest, *, names: Sequence[str] = ()
) -> tuple[str | None, str]:
    """``answer`` as the title to store, or ``None`` and why not.

    ``names`` are the team's, in the forms the request replaced. The reasons
    are for a log's counts and the evaluation's table; they name the rule and
    carry nothing of the text."""
    written = unquoted(answer.strip())
    if "\n" in written or "\r" in written:
        return None, "more than one line"
    title = " ".join(written.split()).rstrip(".")
    if not title:
        return None, "empty"
    if len(title) > TITLE_MAX:
        return None, "over 20 characters"
    if any(mark in title for mark in "[]()…"):
        return None, "a bracket or a cut mark"
    if not _ends_in_a_noun(title, request.kind):
        return None, "does not end in a noun"
    if says_a_pointing_word(title):
        # Its words are the sentence's own, so nothing below would refuse it:
        # a sentence that kept "이거" gives a title of "이거". "그건 확인" too.
        return None, "a pointing word"
    if not _numbers(title) <= _numbers(request.text):
        return None, "a number the sentence does not say"
    if any(name and name in title for name in names):
        return None, "names a person"
    dates = [match.group(0) for match in _DATE.finditer(title)]
    if dates and request.kind == "item":
        return None, "a date on an item"
    if any(date not in request.text for date in dates):
        return None, "a date the sentence does not say"
    if not _own_words(title, request.text):
        return None, "words the sentence does not have"
    return title, ""


def _read(answer: str, count: int) -> dict[int, str]:
    """``{position: title}`` from the model's JSON; an entry that is not one is
    left out and its row gets no title."""
    try:
        data = json.loads(answer)
    except ValueError:
        return {}
    rows = data.get("titles") if isinstance(data, dict) else None
    found: dict[int, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        n, title = row.get("n"), row.get("title")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= count:
            continue
        if isinstance(title, str) and n - 1 not in found:
            found[n - 1] = title
    return found


def _render(batch: Sequence[tuple[TitleRequest, str]]) -> str:
    items = "\n".join(
        f"{n}. {_LABEL[request.kind]} {scrubbed}" for n, (request, scrubbed) in enumerate(batch, 1)
    )
    return _PROMPT.format(items=items)


def _batches(
    rows: Sequence[tuple[int, TitleRequest, str]],
) -> list[list[tuple[int, TitleRequest, str]]]:
    """``rows`` in order, in runs of at most ``MAX_PER_CALL`` whose prompt fits
    one request. A sentence too long for a request by itself is in none."""
    out: list[list[tuple[int, TitleRequest, str]]] = []
    current: list[tuple[int, TitleRequest, str]] = []
    for row in rows:
        if len(_render([(row[1], row[2])])) > _PROMPT_BUDGET:
            continue
        trial = [*current, row]
        if current and (
            len(trial) > MAX_PER_CALL
            or len(_render([(r, s) for _, r, s in trial])) > _PROMPT_BUDGET
        ):
            out.append(current)
            current = [row]
        else:
            current = trial
    if current:
        out.append(current)
    return out


class LlmTitler(GeminiClient):
    """Gemini's ``generateContent`` over a meeting's rows, a batch per call."""

    step = "title"

    def titles(self, requests: Sequence[TitleRequest]) -> list[Title]:
        """One ``Title`` per request, in order. A row the model skipped, and a
        row whose sentence fits no request, has none.

        A failed call and a privacy refusal are raised: the task logs them by
        meeting id and the rows keep no title. Each call is asked once -- a
        refused title is not asked for again, since the row without one shows
        what it showed before."""
        results = [Title(None, "not asked") for _ in requests]
        if not requests:
            return results
        scrubbed, surface = substitute_names_mapped([r.text for r in requests], self._roster)
        names = tuple(surface.values())
        rows = [
            (index, request, text)
            for index, (request, text) in enumerate(zip(requests, scrubbed, strict=True))
            if request.text.strip()
        ]
        calls = 0
        for batch in _batches(rows):
            calls += 1
            body = {
                "contents": [
                    {"role": "user", "parts": [{"text": _render([(r, s) for _, r, s in batch])}]}
                ],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            }
            answers = _read(_answer_text(self._post(body, index=calls)), len(batch))
            for position, (index, request, _) in enumerate(batch):
                answer = answers.get(position)
                if answer is None:
                    results[index] = Title(None, "no answer")
                    continue
                title, reason = accept(answer, request, names=names)
                results[index] = Title(title, reason, answer)
        accepted = sum(1 for result in results if result.text is not None)
        # Counts only. A title is meeting content.
        log.info("extraction_titles_written", calls=calls, rows=len(requests), accepted=accepted)
        return results
