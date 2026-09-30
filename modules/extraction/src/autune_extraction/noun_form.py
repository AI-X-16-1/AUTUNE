"""A spoken sentence, tidied into the noun-ended line a record would carry.

"그럼 제가 다음 주 화요일까지 볼게요" is what was said; "다음 주 화요일까지 볼 예정"
is what goes into Notion or onto a calendar. The original stays in the database
and beside the tidied line on screen, so the reader can always check one against
the other.

**Rules, not a model.** The endings a meeting uses to commit and to decide are a
short closed list, and a rule can be read, tested and predicted, which a
paraphrase cannot -- a generated sentence is wrong in a way the reader cannot
see (``decisions._build``). It also sends nothing anywhere: the text is only
ever rewritten in this process.

**When no rule fits, the sentence stays as it was said.** A sentence with a
negation or a question in it, or an ending this list does not know, is not
rewritten -- a wrong noun form ("안 볼 예정" for "안 볼게요" is right, "예정" for
"안 예정" is not) costs more than an unpolished one, and the person confirming it
sees the original either way.

The output is what a person confirms and may rewrite before anything leaves
(ADR 0006); this module only makes the draft they start from.
"""

from __future__ import annotations

import re

_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")

_LEADING_FILLER = re.compile(
    r"^(?:(?:그럼|그러면|자|네|넵|예|음|어|아|저기|일단|근데|뭐|그니까|그러니까|아무튼)[\s,.…]+)+"
)
"""Discourse markers that open a turn and say nothing about its content. "그" alone
is not here: "그 방향으로" is content."""

_FIRST_PERSON = re.compile(r"(?:(?<=\s)|^)(?:제가|저는|저도|내가|나는)\s+")
"""Who is speaking. A commitment's speaker is its assignee, held in its own field,
and a line that repeats it says nothing the field does not."""

_ACKNOWLEDGEMENT = re.compile(
    r"^(?:네|넵|예|음|알겠습니다|감사합니다|(?:네|넵|예)[\s,]*알겠습니다)$"
)

_NEGATION = re.compile(r"(?:(?<=\s)|^)(?:안|못)\s|않|못\s?[가-힣]+(?:겠습니다|겠어요|게요)")

_QUESTION_END = re.compile(r"(?:까요|나요|ㄹ까|을까|십니까|입니까|어때요|어떨까요)$")

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_RIEUL = 8
"""Index of ㄹ among the 28 final consonants a Hangul syllable can carry."""


def _is_hangul(ch: str) -> bool:
    return _HANGUL_BASE <= ord(ch) <= _HANGUL_LAST


def _final(ch: str) -> int:
    return (ord(ch) - _HANGUL_BASE) % 28


def _with_rieul(stem: str) -> str:
    """The stem followed by the ㄹ that marks a future or intended act.

    보 -> 볼 (the ㄹ joins the last syllable), 만들 -> 만들 (it is already
    there), 먹 -> 먹을 (a syllable that has a final consonant takes 을).
    """
    if not stem or not _is_hangul(stem[-1]):
        return stem
    final = _final(stem[-1])
    if final == 0:
        return stem[:-1] + chr(ord(stem[-1]) + _RIEUL)
    if final == _RIEUL:
        return stem
    return stem + "을"


# The verb of an act a person will do, as the last word of the sentence says it.
_FUTURE_STEM = re.compile(r"^(?P<stem>.+?)(?:겠습니다|겠어요|겠다|겠음)$")
_FUTURE_GAE = re.compile(r"^(?P<form>[가-힣]*[가-힣])(?:게요|께요)$")
_PLAN_WORD = frozenset({"거예요", "거에요", "겁니다", "것입니다", "거고요"})

_AUXILIARY = frozenset({"볼", "놓을", "둘", "드릴", "줄"})
"""``해`` followed by one of these adds nothing to *what* is done: "확인해 볼게요"
and "확인할게요" are the same act."""


def _carries_rieul(word: str) -> bool:
    return (
        bool(word) and _is_hangul(word[-1]) and (_final(word[-1]) == _RIEUL or word.endswith("을"))
    )


def _plan(words: list[str]) -> str | None:
    """A sentence about something the speaker will do, as a noun phrase ending 예정."""
    last = words[-1]
    head = words[:-1]

    form: str | None = None
    if (match := _FUTURE_STEM.match(last)) is not None:
        form = _with_rieul(match["stem"])
    elif (match := _FUTURE_GAE.match(last)) is not None:
        # 볼게요 / 먹을게요 / 만들게요: the ㄹ is already in the word. 하게요 is not
        # this ending, and neither is anything whose stem has no ㄹ to carry.
        if _carries_rieul(match["form"]):
            form = match["form"]
    elif last in _PLAN_WORD and head and _carries_rieul(head[-1]):
        # "…할 거예요": the plan is two words, the verb and 거예요.
        form, head = head[-1], head[:-1]
    if form is None:
        return None

    # 확인해 볼게요 -> 확인 예정
    if form in _AUXILIARY and head and head[-1].endswith("해") and len(head[-1]) >= 2:
        return " ".join([*head[:-1], f"{head[-1][:-1]} 예정"])
    # 정리할 -> 정리 예정: a noun that took 하다 is the noun and nothing else.
    if form.endswith("할") and len(form) >= 2 and _is_hangul(form[-2]):
        return " ".join([*head, f"{form[:-1]} 예정"])
    return " ".join([*head, f"{form} 예정"])


_DECIDED_TO = re.compile(
    r"^(?P<head>.*?하기로)\s*(?:했습니다|했어요|했다|하겠습니다|합니다|합시다|하시죠|하죠|해요|결정했습니다|결정했어요|정했습니다|정했어요)$"
)
_DONE = re.compile(
    r"^(?P<noun>.*?[가-힣]{2,}?)(?:했습니다|했어요|했다|합니다|합시다|하시죠|하죠|해요|한다|하자)$"
)
_BECAME = re.compile(r"^(?P<noun>.*?[가-힣]{2,}?)(?:됐습니다|되었습니다|됩니다|되겠습니다)$")
_GO_WITH = re.compile(r"^(?:가시죠|갑시다|가죠|가겠습니다|갑니다|가요|갈게요)$")
_SETTLE_ON = re.compile(r"^(?:하겠습니다|하죠|합시다|하시죠|할게요|합니다|했습니다)$")


def _record(words: list[str]) -> str | None:
    """A sentence about something settled, as a noun phrase ending 함, 됨, 결정 or 진행."""
    joined = " ".join(words)
    last = words[-1]

    if (match := _DECIDED_TO.match(joined)) is not None:
        return f"{match['head']} 함"

    # "A안으로 가시죠" -> "A안으로 진행"; "A안으로 하겠습니다" -> "A안으로 결정".
    if len(words) >= 2 and words[-2].endswith(("로", "으로")):
        if _GO_WITH.match(last):
            return " ".join([*words[:-1], "진행"])
        if _SETTLE_ON.match(last):
            return " ".join([*words[:-1], "결정"])

    if (match := _BECAME.match(last)) is not None:
        return " ".join([*words[:-1], f"{match['noun']}됨"])
    if (match := _DONE.match(last)) is not None:
        return " ".join([*words[:-1], f"{match['noun']}함"])
    return None


def _tidy_sentence(sentence: str) -> str | None:
    """One sentence, tidied; ``None`` when there is nothing to keep of it.

    A sentence no rule fits comes back exactly as it was said -- not with its
    filler stripped and its verb ending left, which would be neither.
    """
    said = sentence.strip()
    text = said.rstrip(".!…").strip()
    if not text or _ACKNOWLEDGEMENT.match(text):
        return None
    if text.endswith("?") or _QUESTION_END.search(text) or _NEGATION.search(text):
        return said
    text = _LEADING_FILLER.sub("", text)
    text = _FIRST_PERSON.sub("", text).strip()
    if not text:
        return None

    words = text.split()
    tidied = _record(words) or _plan(words)
    return tidied if tidied is not None else said


def tidy(text: str) -> str:
    """The noun-ended form of what was said, or ``text`` unchanged.

    Sentences are tidied one at a time and joined with a space. A turn that is
    nothing but acknowledgement ("네 알겠습니다") has no content to keep, so the
    original comes back rather than an empty string.
    """
    original = text.strip()
    if not original:
        return text
    kept = []
    for sentence in _SENTENCE_BREAK.split(original):
        tidied = _tidy_sentence(sentence)
        if tidied:
            kept.append(tidied)
    return " ".join(kept) if kept else original
