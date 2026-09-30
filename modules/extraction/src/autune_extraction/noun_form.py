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

_NEGATOR = frozenset({"안", "못"})
"""A word that negates the predicate when it stands right before it: "안 볼게요",
"못 갈게요". A "안" further back -- "수량 안 맞는 건은 순서 올리겠습니다" -- negates a
clause inside the sentence, not what the speaker will do."""

_QUESTION_END = re.compile(r"(?:까요|나요|ㄹ까|을까|십니까|입니까|어때요|어떨까요)$")

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_RIEUL = 8
"""Index of ㄹ among the 28 final consonants a Hangul syllable can carry."""


def _is_hangul(ch: str) -> bool:
    return _HANGUL_BASE <= ord(ch) <= _HANGUL_LAST


def _final(ch: str) -> int:
    return (ord(ch) - _HANGUL_BASE) % 28


_D_IRREGULAR = {"듣": "들", "걷": "걸", "깨닫": "깨달", "싣": "실"}
"""ㄷ turns into ㄹ before a vowel: 듣다 -> 들을, not 듣을."""

_B_IRREGULAR = {"돕": "도우", "줍": "주우", "눕": "누우"}
"""ㅂ turns into 우 before a vowel: 돕다 -> 도울. Only the verbs that are always
irregular; 굽다 (roast, irregular) and 굽다 (bend, regular) share a spelling and
are left out."""

_S_IRREGULAR = {"짓": "지", "잇": "이", "붓": "부", "젓": "저", "낫": "나", "긋": "그"}
"""ㅅ drops before a vowel: 짓다 -> 지을. 웃다, 씻다 and 벗다 are regular and are
not here."""

_AMBIGUOUS_STEMS = ("묻",)
"""묻다 is "ask" (물을) and "bury" (묻을) -- one spelling, two forms."""


def _with_rieul(stem: str) -> str | None:
    """The stem followed by the ㄹ that marks a future or intended act.

    보 -> 볼 (the ㄹ joins the last syllable), 만들 -> 만들 (it is already
    there), 먹 -> 먹을 (a syllable that has a final consonant takes 을), and the
    irregular verbs their own way: 듣 -> 들을, 돕 -> 도울, 짓 -> 지을.

    ``None`` when the stem is one whose form cannot be told from the spelling, so
    the caller leaves the sentence as it was said rather than writing "물을 예정"
    for "묻을 예정" or the other way round.
    """
    if not stem or not _is_hangul(stem[-1]):
        return stem
    if stem.endswith(_AMBIGUOUS_STEMS):
        return None
    for table, suffix in ((_D_IRREGULAR, "을"), (_B_IRREGULAR, "ㄹ"), (_S_IRREGULAR, "을")):
        for plain, changed in table.items():
            if stem.endswith(plain):
                base = stem[: -len(plain)] + changed
                return base + suffix if suffix == "을" else _with_rieul(base)
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
_MUST = re.compile(r"^(?P<stem>.+야)겠(?:다|습니다|어요|음)$")
_INTEND = re.compile(r"^(?P<stem>.+?)려고요$")
_EMPTY_FORMS = frozenset({"그럴", "할"})
_KEEP_VERB = frozenset({"말", "이야기", "얘기", "생각"})
"""Nouns whose 하다 verb is a verb in its own right: "말할 예정" is "will say",
and "말 예정" is not anything."""


def _reducible(noun: str) -> bool:
    """Whether "<noun>하다" is a noun and 하다 -- 정리하다, 확인하다 -- so that the
    noun alone says the act. A one-syllable stem ("말하다", "일하다") or one of
    ``_KEEP_VERB`` is a verb by itself and keeps its verb."""
    return len(noun) >= 2 and noun not in _KEEP_VERB and _is_hangul(noun[-1])


_INTEND_HELPER = frozenset({"해요", "합니다"})

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

    # "넣어야겠다" is "has to", not "will": 넣어야 함, never the 넣어얄 the ㄹ rule would make.
    if (match := _MUST.match(last)) is not None:
        return " ".join([*head, match["stem"], "함"])

    # "보려고 해요" is one intention in two words; fold it into "보려고요".
    if last in _INTEND_HELPER and head and head[-1].endswith("려고"):
        last, head = head[-1] + "요", head[:-1]

    form: str | None = None
    if (match := _FUTURE_STEM.match(last)) is not None:
        form = _with_rieul(match["stem"])
    elif (match := _INTEND.match(last)) is not None:
        stem = match["stem"]
        # 먹으려고요: the 으 is the connective a consonant stem takes, not the stem.
        if len(stem) >= 2 and stem.endswith("으") and _is_hangul(stem[-2]) and _final(stem[-2]):
            stem = stem[:-1]
        form = _with_rieul(stem)
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
    # "그럴게요" / "할게요" on their own point at something said before; "그럴 예정"
    # says nothing, so the sentence stays as it was said.
    if not head and form in _EMPTY_FORMS:
        return None

    # 확인해 볼게요 -> 확인 예정
    if form in _AUXILIARY and head and head[-1].endswith("해") and _reducible(head[-1][:-1]):
        return " ".join([*head[:-1], f"{head[-1][:-1]} 예정"])
    # 정리할 -> 정리 예정: a noun that took 하다 is the noun and nothing else.
    if form.endswith("할") and _reducible(form[:-1]):
        return " ".join([*head, f"{form[:-1]} 예정"])
    return " ".join([*head, f"{form} 예정"])


_DECIDED_TO = re.compile(
    r"^(?P<head>.*?기로)\s*(?:했습니다|했어요|했다|했었죠|했었어요|했죠|했지요|하겠습니다|합니다|합시다|하시죠|하죠|해요|결정했습니다|결정했어요|정했습니다|정했어요)$"
)
_DONE = re.compile(
    r"^(?P<noun>.*?[가-힣]{2,}?)(?:했습니다|했어요|했다|합니다|합시다|하시죠|하죠|해요|한다|하자)$"
)
_BECAME = re.compile(r"^(?P<noun>.*?[가-힣]{2,}?)(?:됐습니다|되었습니다|됩니다|되겠습니다)$")
_SETTLED_WORD = re.compile(r"^(?:정했습니다|정했어요|정했다|정했음|정함)$")
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
        if _SETTLED_WORD.match(last):
            return " ".join([*words[:-1], "결정"])
        if _GO_WITH.match(last):
            return " ".join([*words[:-1], "진행"])
        if _SETTLE_ON.match(last):
            return " ".join([*words[:-1], "결정"])

    if (match := _BECAME.match(last)) is not None:
        return " ".join([*words[:-1], f"{match['noun']}됨"])
    if (match := _DONE.match(last)) is not None:
        return " ".join([*words[:-1], f"{match['noun']}함"])
    return None


def _negated(words: list[str]) -> bool:
    """Whether the sentence says the speaker will *not* do something."""
    if len(words) >= 2 and words[-2] in _NEGATOR:
        return True
    # 하지 않겠습니다, 못하겠습니다: the negation is inside the last word.
    return "않" in words[-1] or words[-1].startswith("못")


def _tidy_sentence(sentence: str) -> str | None:
    """One sentence, tidied; ``None`` when there is nothing to keep of it.

    A sentence no rule fits comes back exactly as it was said -- not with its
    filler stripped and its verb ending left, which would be neither.
    """
    said = sentence.strip()
    text = said.rstrip(".!…").strip()
    if not text or _ACKNOWLEDGEMENT.match(text):
        return None
    if text.endswith("?") or _QUESTION_END.search(text):
        return said
    text = _LEADING_FILLER.sub("", text)
    text = _FIRST_PERSON.sub("", text).strip()
    if not text:
        return None

    words = text.split()
    if _negated(words):
        return said
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
