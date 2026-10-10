"""The utterance classifier as a cloud LLM call (``classifier_impl=llm``).

Why it exists: the 2026-09-23 mentoring reset the goal for the remaining weeks
to "everything works end to end, accuracy second", and said to use an LLM
wherever a trained model's accuracy is low. The fine-tuned DeBERTa's commitment
F1 on real Korean speech is ~0.3. This implementation, run against the real API
on the self-authored 8.txt dummy meeting (86 utterances, 16 commitment rows),
scored commitment F1 0.968 with gemini-3.8-flash and 0.909 with
gemini-3.5-flash-lite (2026-09-28); the worked examples in ``INSTRUCTIONS``
took Flash-Lite to 0.938 there and 0.959 on a second dummy meeting.

**What leaves our infrastructure, and why it is allowed** (privacy.md section 6:
masked text only, and only what the feature needs):

- Utterance text only, already PII-masked at write time by module A. No
  speaker, no name, no timestamp, no meeting id, no utterance id -- the prompt
  numbers the lines 1..n within one request, and the answer is mapped back by
  position. Since 2026-10-06 the answer also carries, for a line it labels a
  commitment or a decision, one line saying what it is (``usable_summary``):
  that changes what comes back, not what goes out. The feature needs every
  utterance (it classifies every one), so the meeting goes out, but in
  requests of at most ``MAX_OUTBOUND_CHARS`` like ``HostedDeberta``'s, never
  as one body.
- Only utterances whose speaker consented: ``service.classify_utterances``
  filters before calling ``classify``, the same as for every implementation.
- Every request goes through ``autune_integrations.HttpClient``, so
  ``check_outbound`` scans every string in the body and refuses an unmasked
  phone number, e-mail or account number, and an oversized body.

Names are not masked by module A (there is no pattern for them), so **names
from the meeting team's roster are replaced before any request** (#411): each
member's full name, and the given name of a three-syllable Korean name, becomes
``[사람N]`` -- numbered by first appearance within one ``classify`` call, the
same person the same number, never stored. A label carries no text; a
summary does, and each ``[사람N]`` in one is put back as the name it stood
for before it is kept, as the resolver does (``usable_summary``). Only the request changes; the
database, the resolver and every other output keep the text as it was. What
still leaves: names not on the roster -- people outside the team, nicknames,
English or misheard names. And a roster name that is also a word ("하늘") is
replaced where it is only a word, which costs accuracy, not data. The cloud
resolver replaces roster names the same way (``resolver._scrubbed``, #530); the
Notion and Jira syncs still carry names, to the team's own tools. Enabling
``llm`` outside a demo remains a team decision (#392).

The LLM gives a label, not a probability. ``confidence`` is therefore a fixed
``LLM_CONFIDENCE``: the threshold ADR 0006 compares against is unset by default
(nothing is a candidate), and a made-up spread would be worse than a constant
that says "unscored".
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from typing import Any

from autune_contracts.enums import UtteranceKind
from autune_core import get_logger
from autune_core.errors import PrivacyViolationError
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .base import Prediction

log = get_logger(__name__)

LLM_CONFIDENCE = 0.9
"""What every answer reports as its confidence, a kind or none alike; the rest of
the probability mass goes to "none" for a kind and is spread over nothing for none."""

CONTEXT_LINES = 3
"""Earlier utterances sent before each request's own, marked as context only, so
an acceptance ("네 제가 할게요") can be read against the request before it."""

INSTRUCTIONS = (
    "회의 녹취록의 [대상] 줄마다 종류를 판단하세요.\n"
    "commitment: 화자가 할 일을 맡거나 요청을 수락함(기한 없어도 됨). "
    "decision: 회의가 무엇을 하기로 정함(보류·조건부 포함). "
    "open_question: 정보·의견·행동을 남에게 요청하거나 물음(요청 자체는 이것, 수락은 commitment). "
    "concern: 앞 말에 대한 반대·문제 제기. "
    "ambiguous: '검토해 볼게요'처럼 구체적 약속 없는 약한 동의, 다른 팀이 할 일 전달. "
    "그 외(설명·잡담·맞장구·투표·예상 수치)는 적지 마세요.\n"
    "[문맥] 줄은 판단하지 말고 참고만 하세요. [사람1], [사람2] 같은 표시는 가린 사람 이름입니다. "
    'JSON 한 줄로만 답하세요: {"labels": {"줄번호": "종류", ...}, '
    '"summaries": {"줄번호": "요약", ...}, "parts": {"줄번호": "옮긴 부분", ...}}. '
    '해당 없으면 {"labels": {}}. '
    "summaries에는 commitment와 decision 줄만, 그 줄의 내용을 60자 안팎의 짧은 한 문장으로 "
    "적으세요"
    "(약속: 무엇을 언제까지 하는지, 결정: 무엇을 하기로 했는지). "
    "'그거' 같은 말은 문맥이 가리키는 것으로 바꾸되, 줄과 문맥에 없는 날짜·숫자·이름은 "
    "쓰지 마세요. 줄에 있는 [사람1] 같은 표시는 그대로 옮기고 새로 만들지 마세요. "
    "말한 사람 자신은 주어로 쓰지 말고('제가', '화자는' 없이) 할 일부터 적으세요. "
    "parts에는 같은 줄마다, 그 약속·결정을 말한 "
    "부분만 줄에서 글자 그대로 옮겨 적으세요(고치거나 줄이지 말고, 줄 전체가 그 "
    "내용이면 줄 전체). "
    "한 줄에 대상이 서로 다른 약속·결정이 둘 이상이면(대상마다 기한·담당이 따로) "
    "줄번호에 -1, -2를 붙여 labels·summaries·parts에 하나씩 적으세요. "
    "대상 하나에 동사만 둘이면 나누지 마세요."
    "\n예시(다른 회의):\n"
    "1 [문맥] 이 설문 결과는 누가 정리해 주실래요?\n"
    "2 [대상] 제가 할게요, 목요일까지요. → commitment\n"
    "3 [대상] 캐시 만료 시간을 1시간으로 늘리는 걸 제안드려요. → 적지 않음(제안은 약속 아님)\n"
    "4 [대상] 그럼 수진 님이 로그 확인하고 결과 공유해 주세요. "
    "→ open_question(남에게 시킴, 화자 본인 약속 아님)\n"
    "5 [대상] 저는 영업 입장에서 2안을 밀고 싶어요. → 적지 않음(의견·투표)\n"
    "6 [문맥] 어떤 인력이 필요하세요?\n"
    "7 [대상] 프런트엔드 한 명이요. → 적지 않음(질문에 대한 답)\n"
    "8 [대상] 로그는 제가 화요일까지 정리하고, 문구는 목요일까지 고칠게요. "
    '→ "8-1" commitment, "8-2" commitment(대상이 둘)\n'
    "9 [대상] 시안은 제가 금요일까지 고쳐서 공유할게요. → commitment 하나(대상 하나에 동사 둘)\n"
)
"""Kept short on purpose: ``check_outbound`` counts these characters against the
same 4,000 as the utterances.

The examples are the one prompt change that measured better and steadier on two
dummy meetings (2026-09-28). Without them gemini-3.5-flash-lite -- the fallback
that answers every window once 3.8 Flash's free-tier 20 requests a day are
spent -- keeps recall near 1.0 but labels an answer to a question, a proposal
or a vote as a commitment, and a different set each run even at temperature 0:
commitment F1 0.750-0.970 on 8.txt and 0.721-0.939 on EVAL_02, three runs each.
With them, 0.938 on all three 8.txt runs and 0.939-0.979 on EVAL_02, for fewer
output tokens. The cost: 8.txt's "천천히 만들어볼게요" (#77) was missed in all
three. Lines 3-7 are the four false-positive kinds 8.txt showed, written as
analogues rather than copies; EVAL_02 was not looked at before it was scored.
Raising ``thinkingLevel`` was tried and is not set: low and medium spent no
thinking tokens, medium scored 0.607 on EVAL_02, and high cost more per meeting
than 3.8 Flash. Only commitment was scored -- the gold on both meetings marks
commitments only -- so the effect on the other four kinds is unmeasured.

The last sentences, asking for ``summaries`` (2026-10-06), cost a little
precision, measured the same day on those two meetings with ten runs a cell,
this prompt and the one before it taken in turn: commitment F1 0.961 -> 0.920
on 8.txt and 0.974 -> 0.949 on EVAL_02, the loss being a few lines called a
commitment in most runs (recall unchanged on 8.txt, slightly lower on EVAL_02),
and 8.txt needs three requests where it needed two. They are here because the
summary used to cost a request a commitment to a resolver that rewrites one
sentence and cannot shorten a longer one; on an invented twelve-utterance
meeting the resolver's six to nine requests became none.

**Three rewrites meant to win that precision back were tried and none is
here** (2026-10-06): sharper definitions (a commitment is the speaker's own
task or a request made *to them*; "그럼 확정할게요" is a decision; capability,
routine and an unasked "네, 알겠습니다" are nothing), two more worked examples,
and both together. Three alternated rounds on four dummy meetings: the two
above, which were read while the rewrites were written, and EVAL_03 and
EVAL_04, which were not. On the two that were read every rewrite looked
better (8.txt 0.938 -> 0.98-0.99). On the two that were not, none did: this
prompt 0.942 and 1.000; definitions 0.979 and 0.951; examples 0.958 and
0.981; both 0.900 and 0.981 -- and the longer ones cost a fourth request on
some meetings. A gain that shows only on the meetings the examples were
written from is the examples, not the prompt. The same rounds carried a
probe for an acknowledgement: after a piece of news this prompt calls
"네 알겠습니다." ``ambiguous`` in a seven-line probe and nothing at all after
a 900-character turn (5 of 5); after a request addressed to the speaker it
calls it a commitment, which is what accepting a request is. The rewrites
that made the first case nothing also made the second one nothing, or lost
EVAL_03. So the first case is a fixed rule after this classifier and not a
line of this prompt: ``service.drop_bare_acknowledgements``. Scripts and logs:
``dataset/experiments/2026-10-06-summary-with-labels`` (local).

``parts`` and "60자 안팎의 짧은" (2026-10-08) are here because the user asked
for the part of an utterance an item is about and not the utterance (the same
day), and a sentence is as narrow as cutting can get (``excerpt``). Measured
that day, this prompt and the one before it in turn. On the four meetings
above, two runs a cell: commitment F1 0.941 -> 0.947 (precision 0.906 ->
0.964, recall 0.985 -> 0.934) -- no difference two runs can show, the
direction being fewer lines labelled; one more request on two of the four.
On one invented meeting of twelve turns of 317-412 characters with one promise
or decision planted in each, three runs: the planted sentence labelled 36 of
36 by both. **Every part returned was in its line character for character**
(282 of 282 over both), and on the long turns each held the planted words
whole: a turn of about 360 characters is quoted as a sentence of about 65
without ``parts`` and as about 38 with. Summaries were short before the
wording (mean 28 characters) and are with it (31); the wording is kept because
it is the prompt these numbers are of. Not measured: a transcript without
sentence ends, and a real meeting. What comes back is used only when it is in
the line (``usable_part``); a quotation is never a model's words. Scripts and
logs: ``dataset/experiments/2026-10-08-quoted-part`` (local)."""

_KINDS = {kind.value: kind for kind in UtteranceKind}
_RETRY_BACKOFF_SEC = (2.0, 5.0, 10.0)
"""Longer than ``HostedDeberta``'s: the provider answered 503 three times in a row
within seven seconds on 2026-09-28 -- a busy model, not a broken request."""
_BODY_OVERHEAD = len(INSTRUCTIONS) + 200
"""Instructions plus line markers and JSON punctuation the budget has to leave room for."""


def require_scalar_addressing(value: Any, addressing: frozenset[str]) -> None:
    """Refuse a body where an ``addressing`` key holds anything but a string.

    ``check_outbound`` skips the whole value under an addressing key, not just a
    string, so the exemption is safe only while both keys here stay scalars --
    as ``contents[].role`` and ``generationConfig.responseMimeType`` are in the
    provider's API today. If either ever holds an object, whatever sits inside
    it would leave unchecked; this makes that a refusal instead of a comment
    someone has to remember (mkkim68, review of #405). The message names the
    key only, never the value.
    """
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in addressing and not isinstance(inner, str):
                raise PrivacyViolationError(
                    f"{key!r} is exempt from the outbound check only as a string"
                )
            require_scalar_addressing(inner, addressing)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            require_scalar_addressing(inner, addressing)


def _llm_client(base_url: str, api_key: str, timeout_sec: float) -> Any:
    """The provider as a client the way every other outbound one is written.

    ``addressing`` names the keys whose values steer the request rather than
    carry meeting content, so ``check_outbound`` skips only those -- and only
    while they are strings (``require_scalar_addressing``). The API key travels
    in a header, never in the body or the URL.

    **The read timeout is raised for this client only.** ``HttpClient``'s 10 s
    suits Slack and Notion; a thinking model answering one window took 12-20 s
    against the real API (2026-09-28), so every window timed out, was retried,
    and a timed-out request may still be billed. The shared default stays as it
    is -- this client sets its own.
    """
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class LlmClient(HttpClient):
        service = "extraction-llm-classifier"
        addressing = frozenset({"role", "responseMimeType"})

        def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            require_scalar_addressing(kwargs.get("json"), self.addressing)
            return super().request(method, path, **kwargs)

    client = LlmClient(base_url, headers={"x-goog-api-key": api_key})
    client._client.timeout = timeout_sec  # noqa: SLF001 - httpx's own setter; see above
    return client


PLACEHOLDER = "[사람{n}]"
"""What a roster name becomes in a request (#411)."""

_HANGUL_FULL_NAME = re.compile(r"[가-힣]{3}")
_HANGUL_WORD = re.compile(r"[가-힣]+")


def _variants(name: str) -> list[str]:
    """The forms a person is called by, from the display name as stored.

    A three-syllable Korean name is also called by its given name ("김민경" ->
    "민경"). A display name comes from the account's ``name`` claim, and Korean
    accounts often carry it with a space, in either order ("박 재경", "재경 박")
    while the speech-to-text writes "박재경" and "재경님". So a name of Hangul
    words is also matched joined, and in the other order, and by its given name:
    the word of two syllables or more beside a one-syllable surname. Two-word
    names where neither word is a single syllable get the joined forms but no
    given name -- there is no telling which word it is.

    Over-matching is the cheap mistake here (a surname replaced where it stands
    alone is still a name) and under-matching is the leak, so where the split is
    a guess the guess errs toward replacing.

    Nothing shorter than two characters -- a one-letter "name" would replace
    letters everywhere.
    """
    forms = [name]
    words = name.split()
    if len(words) >= 2 and all(_HANGUL_WORD.fullmatch(word) for word in words):
        joined = "".join(words)
        forms.append(joined)
        if len(words) == 2:
            first, second = words
            forms.append(second + first)
            if len(first) == 1 and len(second) >= 2:
                forms.append(second)
            elif len(second) == 1 and len(first) >= 2:
                forms.append(first)
        elif _HANGUL_FULL_NAME.fullmatch(joined):
            forms.append(joined[1:])
    elif _HANGUL_FULL_NAME.fullmatch(name):
        forms.append(name[1:])
    return [form for form in dict.fromkeys(forms) if len(form) >= 2]


def _forms(roster: Sequence[str]) -> dict[str, set[str]]:
    """Every form a roster member is called by, and whose it is."""
    owners: dict[str, set[str]] = {}
    for name in {" ".join(n.split()) for n in roster if n and n.strip()}:
        for form in _variants(name):
            owners.setdefault(form, set()).add(name)
    return owners


def _names(owners: dict[str, set[str]]) -> re.Pattern[str] | None:
    """What matches a roster name in a text, longest form first; ``None`` with
    no roster."""
    if not owners:
        return None
    return re.compile("|".join(re.escape(f) for f in sorted(owners, key=len, reverse=True)))


def substitute_names(texts: list[str], roster: Sequence[str]) -> list[str]:
    """``texts`` with every roster name replaced by ``[사람N]`` (#411).

    Longest form first, so "김민경" never leaves "김[사람1]". A name is taken with
    its whitespace collapsed, and a spaced one is matched in its joined and
    swapped forms too (``_variants``). Whatever follows a name -- 님, 씨, a
    particle -- stays. The same person is the same number throughout, numbered by
    first appearance so the numbers say nothing about the roster's order or size.
    A given name two members share is its own person here: it cannot be told
    which of them was meant, and either answer would still be a name. No roster,
    no change.
    """
    return substitute_names_mapped(texts, roster)[0]


def substitute_names_mapped(
    texts: list[str], roster: Sequence[str]
) -> tuple[list[str], dict[str, str]]:
    """``substitute_names`` and what each placeholder stood for.

    The second value maps ``[사람N]`` to the form of that person first written in
    ``texts`` -- what the reference resolver needs to put a name back into the
    sentence it stores, since a description is read by the team and a
    placeholder in it would be nonsense. The classifier never reads it: a label
    has no name in it to restore.
    """
    owners = _forms(roster)
    pattern = _names(owners)
    if pattern is None:
        return list(texts), {}
    person = {
        form: next(iter(p)) if len(p) == 1 else f"shared:{form}" for form, p in owners.items()
    }
    numbers: dict[str, int] = {}
    surface: dict[str, str] = {}

    def placeholder(match: re.Match[str]) -> str:
        who = person[match.group(0)]
        marked = PLACEHOLDER.format(n=numbers.setdefault(who, len(numbers) + 1))
        surface.setdefault(marked, match.group(0))
        return marked

    return [pattern.sub(placeholder, text) for text in texts], surface


def restore_names_mapped(text: str, surface: dict[str, str]) -> str | None:
    """``text`` with each ``[사람N]`` put back as the form it stood for in
    ``surface`` -- or ``None`` when ``text`` holds a ``[사람N]`` that ``surface``
    does not: the model wrote a person it was never sent, and there is no name
    to give them. The one place B puts names back; the resolver and the
    classifier's summary both answer through it (#1226).
    """
    if any(marked not in surface for marked in _PLACEHOLDER.findall(text)):
        return None
    return _PLACEHOLDER.sub(lambda m: surface[m.group(0)], text)


_LINE_OVERHEAD = 12
"""A line's number, its ``[대상]`` or ``[문맥]`` marker and the newline."""


def _cost(text: str) -> int:
    """What one line takes of the budget."""
    return len(text) + _LINE_OVERHEAD


_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_SPACE = re.compile(r"\s+")


def pieces(text: str, limit: int) -> list[str]:
    """``text`` in spoken order as pieces of at most ``limit`` characters.

    What ``sentences`` falls back on for speech with no sentence end in reach.
    A piece ends after a sentence when one ends in its second half, else at a
    space, else at the limit; no character is dropped but the whitespace at a
    cut.
    """
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = limit
        for boundary in (_SENTENCE_END, _SPACE):
            ends = [m.start() for m in boundary.finditer(rest, limit // 2, limit + 1)]
            if ends:
                cut = ends[-1]
                break
        out.append(rest[:cut])
        rest = rest[cut:].lstrip()
    if rest or not out:
        out.append(rest)
    return out


LONG_TURN_CHARS = 300
"""A turn longer than this is read sentence by sentence (the user, 2026-10-06).

One person talking for a minute is one utterance, and the instructions above
were written and measured on lines the length of a sentence. Asked about whole,
a long turn got one label for all of it, and whatever was made from it -- an
item's description, a decision's statement -- was the whole turn: the resolver
rewrites one sentence, it does not summarise, and hands back unchanged a
target that is several. A first version cut only a turn too long for one
request, into halves of a request; in a live run on an invented meeting
(2026-10-06) the item made from such a half was 1,512 characters, and a
907-character turn that fitted a request was an item as it stood. Read by
sentence, the same meeting gave each promise and decision as its own line.

Three hundred because a turn of two or three sentences is what the pipeline
was built on and is left alone. Not measured on real speech."""

SENTENCE_CHARS = 200
"""The longest line a long turn is read in. A sentence within it is one line;
speech with no sentence end in reach -- a transcript without punctuation -- is
cut at a space (``pieces``)."""

SHORT_SENTENCE_CHARS = 20
"""A sentence shorter than this ("네.", "그렇죠.") is not asked about alone: it
goes with the sentence after it, which is usually what it was said to."""


def sentences(text: str) -> list[str]:
    """A long turn as the lines it is asked about, in spoken order: its
    sentences, a short one joined to the next (the last to the one before), one
    longer than ``SENTENCE_CHARS`` cut by ``pieces``. No character is dropped
    but the whitespace at a cut."""
    out: list[str] = []
    carried = ""
    for part in _SENTENCE_END.split(text.strip()):
        line = f"{carried} {part}".strip() if carried else part
        if len(line) < SHORT_SENTENCE_CHARS:
            carried = line
            continue
        carried = ""
        out.extend(pieces(line, SENTENCE_CHARS))
    if carried:
        if out:
            out[-1] = f"{out[-1]} {carried}"
        else:
            out.append(carried)
    return out or [text]


_HELD_OPEN, _HELD_CLOSE = "\ue000", "\ue001"
_HELD = re.compile(f"{_HELD_OPEN}(\\d+){_HELD_CLOSE}")


def said_lines(text: str, names: re.Pattern[str] | None) -> list[str]:
    """``sentences(text)``, with no cut inside a roster name.

    A long turn is cut as it was said and each line has its names replaced
    afterwards, so a name that a cut fell inside would be on two lines, matched
    on neither, and leave as it was said: a display name with a space in it
    ("Min Kim", "박 재경") at a cut made at a space, any name at a cut made in
    the middle of unbroken text. So each name is held as one token with no
    space in it while the turn is cut, and put back; a cut that still landed
    inside a token -- only the cut at the limit can -- is undone by joining the
    two lines. Every name is then whole on one line, where
    ``substitute_names`` replaces it (mkkim68, review of #864: substituting
    before cutting was what kept a name off a boundary).
    """
    if names is None:
        return sentences(text)
    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        held.append(match.group(0))
        return f"{_HELD_OPEN}{len(held) - 1}{_HELD_CLOSE}"

    out: list[str] = []
    open_line = ""
    for line in sentences(names.sub(hold, text)):
        open_line += line
        if open_line.count(_HELD_OPEN) == open_line.count(_HELD_CLOSE):
            out.append(open_line)
            open_line = ""
    if open_line:
        out.append(open_line)
    return [_HELD.sub(lambda match: held[int(match.group(1))], line) for line in out]


def strongest(kinds: Sequence[UtteranceKind | None]) -> UtteranceKind | None:
    """The one kind of an utterance whose pieces were labelled apart: the first
    of ``UtteranceKind``'s own order that any piece got -- a commitment before a
    decision, either before a question, a concern or an ambiguous agreement.
    An utterance has one kind here as everywhere, and of a long turn the
    promise or the decision is what the meeting's record is made from.

    This is the kind the utterance is stored and published with -- the
    contract has one per utterance. The pieces keep their own kinds beside it
    (``Prediction.pieces``), and each commitment or decision among them
    becomes an item or a decision of its own (``decisions.in_pieces``)."""
    got = {kind for kind in kinds if kind is not None}
    return next((kind for kind in UtteranceKind if kind in got), None)


def windows(texts: list[str], budget: int) -> list[tuple[int, int, int]]:
    """``(context, start, end)``: the targets ``[start, end)`` and the context
    lines ``[context, start)`` sent before them, together within ``budget``.

    Context gives way first, the line farthest from the target first: up to
    ``CONTEXT_LINES`` are sent, fewer when the turns are long, none when the
    first target leaves no room. Admitting the first target whatever its
    context had already cost put every request of a long-turned meeting over
    the outbound limit, and ``check_outbound`` then failed the meeting.

    A line longer than the budget on its own is in no window, so that no
    request can be over it. ``classify`` reads a turn over ``LONG_TURN_CHARS``
    by sentence and never passes one.
    """
    out: list[tuple[int, int, int]] = []
    start = 0
    while start < len(texts):
        size = _cost(texts[start])
        if size > budget:
            start += 1
            continue
        context = start
        while (
            context > max(0, start - CONTEXT_LINES) and size + _cost(texts[context - 1]) <= budget
        ):
            context -= 1
            size += _cost(texts[context])
        end = start + 1
        while end < len(texts) and size + _cost(texts[end]) <= budget:
            size += _cost(texts[end])
            end += 1
        out.append((context, start, end))
        start = end
    return out


def render(texts: list[str], context: int, start: int, end: int) -> tuple[str, dict[int, int]]:
    """The request's lines, numbered 1..n, and line number -> index into ``texts``."""
    lines: list[str] = []
    targets: dict[int, int] = {}
    for n, i in enumerate(range(context, end), start=1):
        tag = "대상" if i >= start else "문맥"
        lines.append(f"{n} [{tag}] {texts[i]}")
        if i >= start:
            targets[n] = i
    return "\n".join(lines), targets


def parse(answer: str) -> dict[int, UtteranceKind]:
    """``{"labels": {"3": "commitment"}}`` -> ``{3: COMMITMENT}``. Anything else
    in the answer -- prose around the JSON, an unknown kind, a non-number key --
    is dropped rather than guessed at."""
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        return {}
    try:
        labels = json.loads(match.group(0)).get("labels", {})
    except (json.JSONDecodeError, AttributeError):
        return {}
    if not isinstance(labels, dict):
        return {}
    out: dict[int, UtteranceKind] = {}
    for key, value in labels.items():
        kind = _KINDS.get(str(value).strip())
        if kind is not None and str(key).strip().isdigit():
            out[int(str(key).strip())] = kind
    return out


_PIECE_KEY = re.compile(r"(\d+)-(\d+)")


def parse_split(answer: str) -> dict[int, list[tuple[UtteranceKind, str, str]]]:
    """The lines answered as several things: ``{"labels": {"8-1": "commitment",
    "8-2": "decision"}, "summaries": {"8-1": "...", ...}, "parts": {"8-1":
    "...", ...}}`` -> ``{8: [(COMMITMENT, summary, part), (DECISION, ...)]}``,
    in the order of the numbers after the dash.

    Read as ``parse`` reads a whole line's answer: an unknown kind or a key of
    another shape is dropped rather than guessed at. A summary or a part that
    is missing is "". Whether the parts are in the line, and so whether the
    line is cut at all, is the caller's to check (``cut_in_two``)."""
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        return {}
    try:
        read = json.loads(match.group(0))
        labels = read.get("labels", {})
        written = {key: read.get(key, {}) for key in ("summaries", "parts")}
    except (json.JSONDecodeError, AttributeError):
        return {}
    if not isinstance(labels, dict):
        return {}
    found: dict[int, dict[int, UtteranceKind]] = {}
    for key, value in labels.items():
        at = _PIECE_KEY.fullmatch(str(key).strip())
        kind = _KINDS.get(str(value).strip())
        if at is not None and kind is not None:
            found.setdefault(int(at.group(1)), {})[int(at.group(2))] = kind

    def text_of(name: str, line: int, piece: int) -> str:
        texts = written[name]
        value = texts.get(f"{line}-{piece}") if isinstance(texts, dict) else None
        return " ".join(value.split()) if isinstance(value, str) else ""

    return {
        line: [
            (kind, text_of("summaries", line, piece), text_of("parts", line, piece))
            for piece, kind in sorted(kinds.items())
        ]
        for line, kinds in found.items()
    }


SUMMARISED = (UtteranceKind.COMMITMENT, UtteranceKind.DECISION)
"""The kinds the request asks a one-line summary of: what an action item and
a decision are made from."""

SUMMARY_MAX_CHARS = 120
"""A summary longer than this is not a line; it is dropped for the sentence
as said."""

_PLACEHOLDER = re.compile(r"\[사람\d+\]")

_MARK_LEFT = re.compile(r"\[\s*사람[^\]]*\]")
"""A name mark still in a sentence once every numbered one is put back. The
instructions used to explain the mark by its general form, "[사람N]", and the
model wrote that form back as somebody it had no name for: on dev, 2026-10-09,
two items' sentences began with it. They show numbered ones now, and this
stays, because a model can still make a mark up. Only a mark with a number
stands for a name; any other spelling stands for nobody, and no screen can
read it as a person."""

_LINE_MARKER_LEFT = re.compile(r"\[\s*(?:대상|문맥)\s*\]")
"""The request's own line marker, written into a summary. "[대상]" and "[문맥]"
say which lines of the request to judge; they are no part of what anybody
said. Measured on the four invented meetings, 2026-10-09, three rounds an arm:
today's instructions wrote none in 367 summaries, the ones that show the name
mark by number wrote "[대상]" in 4 of 361 and in 5 of 364 -- always that word,
always in place of whatever the line was about. A summary with one says less
than its line, and is the resolver's like the others below."""

_POINTING_WORD = re.compile(
    r"(?<![0-9A-Za-z가-힣])(?:"
    r"(?:이거|그거|저거|이것|그것|저것)(?!저것|저거)"
    r"|[이그저][건걸게](?:요|로|도|만)?(?![0-9A-Za-z가-힣])"
    r")"
)
"""A word that points at something and names nothing (module B's owner,
2026-10-09).

이거, 그거, 저거, 이것, 그것, 저것, with whatever is attached ("이거를",
"그것은", "이거예요"): at the start of a word only, so the "이거나" that ends
another word is not this, nor is "이것저것", which means several things and
points at none.

And the same words run together with their particle -- 이건, 그건, 저건, 이걸,
그걸, 저걸, 이게, 그게, 저게 -- as a whole word, with at most "요", "로", "도"
or "만" after it ("그걸로"). Whole, because these are also how a name starts:
"이건희" is a person, and a summary gets its names back before it is read
here."""


def says_a_pointing_word(text: str) -> bool:
    """Whether ``text`` holds a word that only points (``_POINTING_WORD``).

    A summary and a title are read on their own -- on a card, in a message,
    by another module -- where there is nothing for such a word to point at."""
    return _POINTING_WORD.search(text) is not None


def parse_summaries(answer: str) -> dict[int, str]:
    """``{"summaries": {"3": "..."}}`` -> ``{3: "..."}``, each on one line.
    Anything else is dropped rather than guessed at, as in ``parse``."""
    return _written(answer, "summaries")


def parse_parts(answer: str) -> dict[int, str]:
    """``{"parts": {"3": "..."}}`` -> ``{3: "..."}``, read as
    ``parse_summaries`` reads its own."""
    return _written(answer, "parts")


def _written(answer: str, key: str) -> dict[int, str]:
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        return {}
    try:
        written = json.loads(match.group(0)).get(key, {})
    except (json.JSONDecodeError, AttributeError):
        return {}
    if not isinstance(written, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in written.items():
        if isinstance(value, str) and str(key).strip().isdigit():
            out[int(str(key).strip())] = " ".join(value.split())
    return out


_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))


def unquoted(text: str) -> str:
    """``text`` without the quotation marks a model put around its answer.

    Around the *whole* answer, or one left over at an end with no partner.
    A mark that opens a phrase the sentence quotes stays with the one that
    closes it: stripping every mark off both ends, as this used to, turned
    ``"처리 중입니다" 로딩 문구는 제가 …`` into ``처리 중입니다" 로딩 문구는 제가 …``
    (seen 2026-10-08 on an invented line) -- a sentence that starts by quoting
    what the meeting said is an ordinary one.
    """
    text = text.strip()
    for opening, closing in _QUOTE_PAIRS:
        inner = text[1:-1]
        wrapped = len(text) >= 2 and text[0] == opening and text[-1] == closing
        if wrapped and opening not in inner and closing not in inner:
            return inner.strip()
        if text.startswith(opening) and closing not in text[1:]:
            return text[1:].strip()
        if text.endswith(closing) and opening not in text[:-1]:
            return text[:-1].strip()
    return text


def usable_summary(written: str, surface: dict[str, str], window: str) -> str:
    """What the model wrote about a line, as the sentence to show for it -- or
    "" when it should not be shown, and the line as said is used instead.

    The model writes this in the same answer as the label, with nothing
    checking it but this. So: one line of at most ``SUMMARY_MAX_CHARS``; every
    placeholder put back as the name it stood for, and a placeholder that was
    never sent means an invented person; and every number and named person in
    it also in ``window`` -- the line and the few said just before it, as they
    were said -- which is the resolver's own groundedness check
    (``resolver._grounded``),
    for the same reason: a date or a name the meeting never said is worse on a
    card than a long sentence.

    And nothing that stands for something the sentence does not say (module
    B's owner, 2026-10-09): no name mark left once the numbered ones are back
    (``_MARK_LEFT``), no line marker of the request (``_LINE_MARKER_LEFT``),
    and no word that only points (``says_a_pointing_word``) -- the
    instructions ask for "그거" to be replaced by what it meant, and a
    summary that kept it says less than the line it is of. Each time the
    answer is "": a commitment or a decision without a summary is what the
    resolver is asked about, and it reads further back and further on than
    this request did, and gives the line as it was said when it cannot tell.
    """
    # Here and not at the top: ``resolver`` imports this module.
    from .resolver import _grounded  # noqa: PLC0415

    text = unquoted(written)
    if not text or len(text) > SUMMARY_MAX_CHARS:
        return ""
    restored = restore_names_mapped(text, surface)
    if restored is None:
        return ""
    # On what the model wrote, before any name is put back: the marker is the
    # request's, and a name is not the model's writing (review of #1207).
    if _LINE_MARKER_LEFT.search(text):
        return ""
    if _MARK_LEFT.search(restored):
        return ""
    if says_a_pointing_word(restored):
        return ""
    return restored if _grounded(restored, window) else ""


def usable_part(written: str, line: str, names: re.Pattern[str] | None) -> str:
    """The words of ``line`` the model says carry the promise or the decision,
    as they stand in ``line`` -- or "" when there are none to use.

    ``written`` is what came back for the line; ``line`` is the line as it was
    said, which is not quite what was sent: the roster's ``names`` went as
    placeholders. So ``written`` is looked for in ``line`` character for
    character, whitespace aside, with a placeholder standing for a name of the
    roster, and what is returned is **cut from ``line``**, never taken from the
    answer. A part the model reworded, shortened, or took from another line is
    not found and gives "", and so does one that is all of the line: there is
    then nothing narrower than the line to quote.

    A person shown a quotation is shown what was said (``excerpt``). This is
    the one place a model's choice of words could become one, so it only ever
    chooses where to cut.
    """
    found = _where(written, line, names)
    if found is None:
        return ""
    return "" if found.group(0).strip() == line.strip() else found.group(0)


def _where(written: str, line: str, names: re.Pattern[str] | None) -> re.Match[str] | None:
    """Where ``written`` stands in ``line``, as ``usable_part`` looks for it."""
    text = written.strip().strip("\"'“”‘’").strip()
    if not text:
        return None
    pattern: list[str] = []
    for token in re.split(r"(\[사람\d+\])", text):
        if _PLACEHOLDER.fullmatch(token):
            if names is None:
                # No name was replaced, so no placeholder was sent.
                return None
            pattern.append(f"(?:{names.pattern})")
        else:
            pattern.extend(re.escape(char) for char in token if not char.isspace())
    if not pattern:
        return None
    return re.search(r"\s*".join(pattern), line)


def cut_in_two(
    answered: Sequence[tuple[UtteranceKind, str, str]], line: str, names: re.Pattern[str] | None
) -> list[tuple[str, UtteranceKind, str]]:
    """A line the model answered as several things (``parse_split``), as the
    pieces it is read in from here on: ``(words, kind, summary)`` in spoken
    order -- or ``[]`` when the answer cannot be used to cut the line.

    Each piece is **cut from ``line``** where the model's ``part`` stands in it,
    as a quotation is (``usable_part``): the model chooses where to cut and
    never the words. The answer is used only when it names at least two
    pieces, every part is found in the line, and no two overlap; anything less
    and the line stays one line (the caller gives it the strongest of the
    kinds, so a label is never lost to a bad cut). Words of the line outside
    every part belong to no piece.

    Not the same thing as a long turn's pieces (``sentences``), which are cut
    by rule at sentence ends and cover the turn: these are cut where a model
    said, inside a sentence, because "…는 화요일까지 정리하고, …는 목요일까지
    고치겠습니다" is two things to do and one sentence (module B's owner,
    2026-10-09: two rows when the objects are two; one when one object has two
    verbs)."""
    if len(answered) < 2:
        return []
    cuts: list[tuple[int, int, UtteranceKind, str]] = []
    for kind, summary, part in answered:
        found = _where(part, line, names)
        if found is None:
            return []
        cuts.append((found.start(), found.end(), kind, summary))
    cuts.sort(key=lambda cut: cut[0])
    if any(later[0] < earlier[1] for earlier, later in zip(cuts, cuts[1:], strict=False)):
        return []
    return [(line[start:end], kind, summary) for start, end, kind, summary in cuts]


def _prediction(
    kind: UtteranceKind | None,
    pieces: tuple[tuple[str, UtteranceKind | None], ...] = (),
    *,
    summary: str = "",
    piece_summaries: tuple[str, ...] = (),
    part: str = "",
    piece_parts: tuple[str, ...] = (),
) -> Prediction:
    if kind is None:
        return Prediction(
            kind=None,
            confidence=LLM_CONFIDENCE,
            scores=dict.fromkeys(UtteranceKind, 0.0),
            none_score=LLM_CONFIDENCE,
            pieces=pieces,
            piece_summaries=piece_summaries,
            piece_parts=piece_parts,
        )
    scores = dict.fromkeys(UtteranceKind, 0.0)
    scores[kind] = LLM_CONFIDENCE
    return Prediction(
        kind=kind,
        confidence=LLM_CONFIDENCE,
        scores=scores,
        none_score=1.0 - LLM_CONFIDENCE,
        pieces=pieces,
        summary=summary,
        piece_summaries=piece_summaries,
        part=part,
        piece_parts=piece_parts,
    )


_USAGE_COUNTS = (
    ("prompt_tokens", "promptTokenCount"),
    ("output_tokens", "candidatesTokenCount"),
    ("thinking_tokens", "thoughtsTokenCount"),
)


def _usage(response: Any) -> dict[str, int]:
    """The token counts the provider returned with an answer
    (``usageMetadata``), under the names they are logged by -- and nothing
    else of the answer.

    Only a whole number is taken: this is read from a response that also holds
    what a model wrote about a meeting, and a count is the one thing in it a
    log line may carry. A block that is missing, or is not what the provider
    documents, gives no counts; a count that is missing is left out (a model
    that does not think returns none for it).
    """
    block = response.get("usageMetadata") if isinstance(response, dict) else None
    if not isinstance(block, dict):
        return {}
    counts: dict[str, int] = {}
    for name, key in _USAGE_COUNTS:
        value = block.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            counts[name] = value
    return counts


class GeminiClient:
    """Gemini's ``generateContent``: the client, the retry, the fallback model and
    the roster every request is scrubbed with (#411).

    Shared by ``LlmClassifier`` and ``LlmResolver`` so that both retry, fall back
    and record ``model_version`` the same way -- and so that neither can send a
    request the other's guard would have refused.
    """

    step = "llm"
    """Which step of an extraction asks, as ``extraction_llm_usage`` names it:
    each subclass says its own."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_sec: float = 60.0,
        fallback_model: str = "",
    ) -> None:
        self._client = _llm_client(base_url, api_key, timeout_sec)
        self._model = model
        self._fallback = fallback_model
        self._roster: tuple[str, ...] = ()
        self.last_model = model
        """The model that answered the latest request -- the second one when the first
        stayed unavailable, so a caller does not ask it again for the same thing."""

    def use_roster(self, names: Sequence[str]) -> None:
        """The meeting team's display names, replaced in every request (#411).
        Set per meeting by the task (``base.give_roster``); empty means none."""
        self._roster = tuple(names)

    @property
    def model_version(self) -> str:
        """``llm:<model>``, or ``llm:<model>+<fallback>`` when a fallback is set.

        With a fallback, any window may have been answered by either model, so
        the pair is what produced the meeting's rows -- recording only the first
        would attribute the second's answers to it."""
        return f"llm:{self._model}" + (f"+{self._fallback}" if self._fallback else "")

    def _answered(self, model: str, response: Any, *, index: int) -> Any:
        """``response``, with what it cost logged: one ``extraction_llm_usage``
        a request that was answered, by the model that answered it.

        What a meeting costs was an estimate from character counts (2026-10-08);
        these are the provider's own numbers. Counts, the model and the step
        only -- no meeting, as no line of this client carries one, and never
        anything the request or the answer said. An answer without counts logs
        nothing and is the answer all the same.
        """
        counts = _usage(response)
        if counts:
            log.info("extraction_llm_usage", step=self.step, model=model, window=index, **counts)
        return response

    def _post_to(self, model: str, body: dict[str, Any], *, index: int) -> Any:
        """Same retry shape as ``HostedDeberta._post``: transient failures only."""
        path = f"/models/{model}:generateContent"
        for attempt, wait in enumerate(_RETRY_BACKOFF_SEC, start=1):
            try:
                response = self._client.request("POST", path, json=body)
                return self._answered(model, response, index=index)
            except TransientIntegrationError as exc:
                # Counts and a reason only; the body is utterances.
                log.info(
                    "extraction_llm_retry",
                    model=model,
                    window=index,
                    attempt=attempt,
                    reason=str(exc),
                )
                time.sleep(wait)
        return self._answered(model, self._client.request("POST", path, json=body), index=index)

    def _post(self, body: dict[str, Any], *, index: int) -> Any:
        """The primary model, then -- if it stays unavailable -- the fallback.

        Only a *transient* failure falls back (a 429, a 5xx, a timeout): the
        request was fine and the model was busy. A 4xx or a privacy refusal is
        about the request itself and would fail the same way on any model."""
        try:
            answer = self._post_to(self._model, body, index=index)
            self.last_model = self._model
            return answer
        except TransientIntegrationError:
            if not self._fallback:
                raise
            log.warning(
                "extraction_llm_fallback", model=self._model, fallback=self._fallback, window=index
            )
            answer = self._post_to(self._fallback, body, index=index)
            self.last_model = self._fallback
            return answer


class LlmClassifier(GeminiClient):
    """Gemini's ``generateContent`` over masked utterances, one window at a time."""

    step = "classifier"

    unread_windows = 0
    """How many windows of the last ``classify`` could not be read, when some
    could. Their lines carry no label, which is not "nothing was said there":
    the run stores what was read and says a part was not
    (``attempts.note_partly_unread``). A count -- not which windows, and
    nothing of them."""

    def _readable(self, body: dict[str, Any], *, index: int) -> tuple[str, str]:
        """This window's answer as text that says something -- labels, or that
        there are none -- or why it says nothing (``unreadable``), after asking
        ``UNREADABLE_ASKS`` times in all. One of the two is "".

        An answer that says nothing used to label nothing, which is what "no
        commitment and no decision in these lines" also looks like: a meeting
        whose one request was refused ended as an extraction that went through
        and found nothing, and nothing tried again (the user, 2026-10-08)."""
        cause = ""
        for ask in range(1, UNREADABLE_ASKS + 1):
            response = self._post(body, index=index)
            answer = _answer_text(response)
            cause = unreadable(response, answer)
            if not cause:
                return answer, ""
            # The cause, the provider's own reason word and counts: an answer
            # that cannot be read can still be utterances.
            log.warning(
                "extraction_llm_unreadable",
                model=self.last_model,
                window=index,
                ask=ask,
                cause=cause,
                finish=_finish_reason(response),
            )
        return "", cause

    def classify(self, texts: list[str]) -> list[Prediction]:
        self.unread_windows = 0
        if not texts:
            return []
        # A long turn is cut as it was said, and each piece keeps those words:
        # an item or a decision is written from the piece, in the database's
        # text. Only the request carries the placeholders, and the budget is
        # counted on it. No cut falls inside a name (``said_lines``), so every
        # name is whole on the line it is replaced in.
        budget = MAX_OUTBOUND_CHARS - _BODY_OVERHEAD
        names = _names(_forms(self._roster))
        owners, said = _in_pieces(texts, names)
        lines, surface = substitute_names_mapped(said, self._roster)
        labels: list[UtteranceKind | None] = [None] * len(lines)
        summaries = [""] * len(lines)
        chosen = [""] * len(lines)
        # A line the model answered as several things, cut where it said.
        split: dict[int, list[tuple[str, UtteranceKind, str]]] = {}
        asked, unread, cause = 0, 0, ""
        for index, (context, start, end) in enumerate(windows(lines, budget)):
            text, targets = render(lines, context, start, end)
            body = {
                "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            }
            asked += 1
            answer, cause_here = self._readable(body, index=index)
            if cause_here:
                # The other windows are still asked: what they hold is kept
                # (the user, 2026-10-08), and only a meeting none of whose
                # windows could be read is a call that failed.
                unread, cause = unread + 1, cause_here
                continue
            for line, kind in parse(answer).items():
                if line in targets:
                    labels[targets[line]] = kind
            # The summary of a line is kept only with a label that takes one,
            # and only if nothing in it is new to that line and the
            # ``CONTEXT_LINES`` said just before it -- what the instructions
            # call its context. Not the whole request: that would let another
            # item's date or name, from a target line further up or down the
            # same request, pass as this one's (PARK, review of #880).
            for line, written in parse_summaries(answer).items():
                at = targets.get(line)
                if at is not None and labels[at] in SUMMARISED:
                    window = " ".join(said[max(context, at - CONTEXT_LINES) : at + 1])
                    summaries[at] = usable_summary(written, surface, window)
            # The part of a line is kept the same way, and only as words that
            # are in the line as it was said (``usable_part``).
            for line, written in parse_parts(answer).items():
                at = targets.get(line)
                if at is not None and labels[at] in SUMMARISED:
                    chosen[at] = usable_part(written, said[at], names)
            # A line answered as "8-1", "8-2" is two things said in one line.
            # It is cut only where the parts stand in it; an answer that cannot
            # cut it still labels it, as one line.
            #
            # A piece's summary is checked against the piece and the lines
            # before the line -- not the rest of the line. The line is cut
            # because each thing in it has its own date and owner, and against
            # the whole line one piece's summary could carry the other's date,
            # which is what the check is there to stop (PARK, review of #880).
            # A name the two pieces share and only the first says is lost the
            # same way: that summary is dropped for the piece as it was said.
            for line, answered in parse_split(answer).items():
                at = targets.get(line)
                if at is None:
                    continue
                before = " ".join(said[max(context, at - CONTEXT_LINES) : at])
                cut = cut_in_two(answered, said[at], names)
                labels[at] = strongest([labels[at], *(kind for kind, _, _ in answered)])
                if cut:
                    split[at] = [
                        (
                            words,
                            kind,
                            usable_summary(summary, surface, f"{before} {words}")
                            if kind in SUMMARISED
                            else "",
                        )
                        for words, kind, summary in cut
                    ]
                    summaries[at], chosen[at] = "", ""
        if unread == asked:
            # Nothing was read at all: there is no result to keep, and "found
            # nothing" would be false. The run is counted and tried again.
            raise UnreadableAnswerError(cause)
        self.unread_windows = unread
        parts: list[list[int]] = [[] for _ in texts]
        for line, owner in enumerate(owners):
            parts[owner].append(line)
        predictions = []
        for own in parts:
            # What the utterance is read in: its lines, a line the model cut
            # being the pieces it was cut into. ``(words, kind, summary, part)``.
            read: list[tuple[str, UtteranceKind | None, str, str]] = []
            for line in own:
                if line in split:
                    read.extend((words, kind, summary, "") for words, kind, summary in split[line])
                else:
                    read.append((said[line], labels[line], summaries[line], chosen[line]))
            several = len(read) > 1
            predictions.append(
                _prediction(
                    strongest([kind for _, kind, _, _ in read]),
                    tuple((words, kind) for words, kind, _, _ in read) if several else (),
                    summary="" if several else read[0][2],
                    piece_summaries=tuple(summary for _, _, summary, _ in read) if several else (),
                    part="" if several else read[0][3],
                    piece_parts=tuple(part for _, _, _, part in read) if several else (),
                )
            )
        # Counts only: the lines are utterances.
        log.info(
            "extraction_llm_classified",
            utterances=len(texts),
            labelled=sum(p.kind is not None for p in predictions),
            in_pieces=sum(len(own) > 1 for own in parts),
            split=len(split),
            summarised=sum(bool(written) for written in summaries),
            narrowed=sum(bool(words) for words in chosen),
            windows=asked,
            unread=unread,
        )
        return predictions


def _in_pieces(texts: list[str], names: re.Pattern[str] | None) -> tuple[list[int], list[str]]:
    """The lines to ask about, as they were said, and for each the index of
    the utterance it is (a piece of). An utterance of up to ``LONG_TURN_CHARS``
    is one line, unchanged; a longer one is its sentences, cut around the
    roster's ``names`` (``said_lines``)."""
    owners: list[int] = []
    lines: list[str] = []
    for index, text in enumerate(texts):
        for piece in said_lines(text, names) if len(text) > LONG_TURN_CHARS else [text]:
            owners.append(index)
            lines.append(piece)
    return owners, lines


UNREADABLE_ASKS = 2
"""How many times one window is asked before its answer is given up on. The
second ask is at once: nothing was busy, the answer was no answer. A run with
a window still unread is tried again by the sweep, so a window that is refused
every time is asked ``UNREADABLE_ASKS * attempts.MAX_ATTEMPTS`` times and then
left."""

BLOCKED = "blocked"
NO_CANDIDATE = "no_candidate"
EMPTY = "empty"
UNPARSEABLE = "unparseable"


class UnreadableAnswerError(TransientIntegrationError):
    """The model answered every window of a meeting with nothing that can be
    read: the request was refused, no candidate came back, the candidate was
    empty, or its text was not the JSON asked for. Carries the last cause.

    A meeting some of whose windows were read is not this: it is a result with
    a part missing (``LlmClassifier.unread_windows``).

    Transient in the shared client's sense -- the request was fine and asking
    again can work -- so whatever handles a busy model handles this. The
    message is one of four words and nothing of the answer.
    """

    def __init__(self, cause: str) -> None:
        super().__init__(f"the model's answer could not be read ({cause})", cause=cause)
        self.cause = cause


def unreadable(body: Any, answer: str) -> str:
    """Why this response says nothing, or "" when it can be read.

    Readable is narrower than "has labels": ``{"labels": {}}`` is the answer
    the instructions ask for when no line is a commitment or a decision, and it
    is a success. So is any JSON object that names no labels (``{}``,
    ``"labels": []``, ``"labels": null``) -- a model saying "none" in the wrong
    shape is still saying none, and failing those would retry a quiet meeting
    for ever. Entries ``parse`` cannot use are dropped there, as before.

    Unreadable is what cannot be told apart from silence: no text at all, text
    with no JSON object in it (cut off, or prose), or labels that hold
    something in a shape nobody can map to lines.
    """
    if not answer.strip():
        feedback = body.get("promptFeedback") if isinstance(body, dict) else None
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            return BLOCKED
        candidates = body.get("candidates") if isinstance(body, dict) else None
        return EMPTY if candidates else NO_CANDIDATE
    match = re.search(r"\{.*\}", answer, re.S)
    try:
        loaded = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        loaded = None
    if not isinstance(loaded, dict):
        return UNPARSEABLE
    labels = loaded.get("labels")
    return UNPARSEABLE if labels and not isinstance(labels, dict) else ""


_REASON_WORD = re.compile(r"[A-Z_]{1,40}")


def _finish_reason(body: Any) -> str:
    """The provider's reason word for the log -- ``SAFETY``, ``MAX_TOKENS`` --
    or "". Only a word of capitals is passed on; anything else is not a reason
    word and could be anything."""
    try:
        reason = body["promptFeedback"]["blockReason"]
    except (KeyError, IndexError, TypeError):
        try:
            reason = body["candidates"][0]["finishReason"]
        except (KeyError, IndexError, TypeError):
            return ""
    return reason if isinstance(reason, str) and _REASON_WORD.fullmatch(reason) else ""


def _answer_text(body: Any) -> str:
    """The first candidate's text, or "" for a blocked or empty answer. What ""
    means is the caller's: the classifier does not take it for "nothing found"
    (``unreadable``)."""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        return ""
