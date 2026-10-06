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
  position. The feature needs every utterance (it classifies every one), so the
  meeting goes out, but in requests of at most ``MAX_OUTBOUND_CHARS`` like
  ``HostedDeberta``'s, never as one body.
- Only utterances whose speaker consented: ``service.classify_utterances``
  filters before calling ``classify``, the same as for every implementation.
- Every request goes through ``autune_integrations.HttpClient``, so
  ``check_outbound`` scans every string in the body and refuses an unmasked
  phone number, e-mail or account number, and an oversized body.

Names are not masked by module A (there is no pattern for them), so **names
from the meeting team's roster are replaced before any request** (#411): each
member's full name, and the given name of a three-syllable Korean name, becomes
``[사람N]`` -- numbered by first appearance within one ``classify`` call, the
same person the same number, never stored and never mapped back (the answer
is a label per line and carries no text). Only the request changes; the
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
    "[문맥] 줄은 판단하지 말고 참고만 하세요. [사람N]은 가린 사람 이름입니다. "
    'JSON 한 줄로만 답하세요: {"labels": {"줄번호": "종류", ...}}. 해당 없으면 {"labels": {}}.'
    "\n예시(다른 회의):\n"
    "1 [문맥] 이 설문 결과는 누가 정리해 주실래요?\n"
    "2 [대상] 제가 할게요, 목요일까지요. → commitment\n"
    "3 [대상] 캐시 만료 시간을 1시간으로 늘리는 걸 제안드려요. → 적지 않음(제안은 약속 아님)\n"
    "4 [대상] 그럼 수진 님이 로그 확인하고 결과 공유해 주세요. "
    "→ open_question(남에게 시킴, 화자 본인 약속 아님)\n"
    "5 [대상] 저는 영업 입장에서 2안을 밀고 싶어요. → 적지 않음(의견·투표)\n"
    "6 [문맥] 어떤 인력이 필요하세요?\n"
    "7 [대상] 프런트엔드 한 명이요. → 적지 않음(질문에 대한 답)\n"
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
commitments only -- so the effect on the other four kinds is unmeasured."""

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


def _prediction(
    kind: UtteranceKind | None, pieces: tuple[tuple[str, UtteranceKind | None], ...] = ()
) -> Prediction:
    if kind is None:
        return Prediction(
            kind=None,
            confidence=LLM_CONFIDENCE,
            scores=dict.fromkeys(UtteranceKind, 0.0),
            none_score=LLM_CONFIDENCE,
            pieces=pieces,
        )
    scores = dict.fromkeys(UtteranceKind, 0.0)
    scores[kind] = LLM_CONFIDENCE
    return Prediction(
        kind=kind,
        confidence=LLM_CONFIDENCE,
        scores=scores,
        none_score=1.0 - LLM_CONFIDENCE,
        pieces=pieces,
    )


class GeminiClient:
    """Gemini's ``generateContent``: the client, the retry, the fallback model and
    the roster every request is scrubbed with (#411).

    Shared by ``LlmClassifier`` and ``LlmResolver`` so that both retry, fall back
    and record ``model_version`` the same way -- and so that neither can send a
    request the other's guard would have refused.
    """

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

    def _post_to(self, model: str, body: dict[str, Any], *, index: int) -> Any:
        """Same retry shape as ``HostedDeberta._post``: transient failures only."""
        path = f"/models/{model}:generateContent"
        for attempt, wait in enumerate(_RETRY_BACKOFF_SEC, start=1):
            try:
                return self._client.request("POST", path, json=body)
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
        return self._client.request("POST", path, json=body)

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

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        # A long turn is cut as it was said, and each piece keeps those words:
        # an item or a decision is written from the piece, in the database's
        # text. Only the request carries the placeholders, and the budget is
        # counted on it. No cut falls inside a name (``said_lines``), so every
        # name is whole on the line it is replaced in.
        budget = MAX_OUTBOUND_CHARS - _BODY_OVERHEAD
        owners, said = _in_pieces(texts, _names(_forms(self._roster)))
        lines = substitute_names(said, self._roster)
        labels: list[UtteranceKind | None] = [None] * len(lines)
        for index, (context, start, end) in enumerate(windows(lines, budget)):
            text, targets = render(lines, context, start, end)
            body = {
                "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            }
            answer = _answer_text(self._post(body, index=index))
            for line, kind in parse(answer).items():
                if line in targets:
                    labels[targets[line]] = kind
        parts: list[list[int]] = [[] for _ in texts]
        for line, owner in enumerate(owners):
            parts[owner].append(line)
        predictions = [
            _prediction(
                strongest([labels[line] for line in own]),
                tuple((said[line], labels[line]) for line in own) if len(own) > 1 else (),
            )
            for own in parts
        ]
        # Counts only: the lines are utterances.
        log.info(
            "extraction_llm_classified",
            utterances=len(texts),
            labelled=sum(p.kind is not None for p in predictions),
            in_pieces=sum(len(own) > 1 for own in parts),
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


def _answer_text(body: Any) -> str:
    """The first candidate's text, or "" -- a blocked or empty answer labels nothing."""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        return ""
