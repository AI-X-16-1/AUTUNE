"""Whether an uploaded file is marked confidential (#817 point 9).

A marked file stops the upload: nothing of it is stored, and one
``ext_material_alarms`` row says that a file was stopped, when, and on which
list its marking was -- never the word, where it was or the file's name.

**Words, not meaning, and only in some places.** The words are looked for in
the file's name and the document's head, not its body
(``docs/modules/extraction.md``, "Where a marking is looked for"). For the
formats read today -- ``.txt``, ``.md``, ``.csv`` -- the head is the first five
lines that say anything; those formats have no footer and no properties. A
marking that sits only in the body, line six on, is not seen. In a file name
``_`` separates words as a space does (``plan_confidential.txt``).

**The list** is the one ``docs/modules/extraction.md`` gives, and it is the
whole of it. Korean: 대외비, 사외비, 극비, 기밀, 보안문서, 비밀문서, 1급·2급·3급
비밀 (the numeral also as Ⅰ, Ⅱ, Ⅲ) -- each with a space allowed between its
parts -- and 사내 한정 (or 사내한정), 내부용. English, in any case:
Confidential, Strictly Confidential, Top Secret, Do Not Distribute, Company
Secret, Internal Use Only, Internal Only, For Internal Use. Not markings on
purpose: 비밀, secret, 내부, internal alone (``Internal API design.md`` is not
stopped).

What this reads is a parameter and a local: the matched word is not returned,
logged or put in an exception -- only the list it was on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

Marking = Literal["korean_marking", "english_marking"]

HEAD_LINES = 5
"""How many lines that say anything make the head of a text file."""


def _spaced(word: str) -> str:
    """``word`` with any whitespace allowed between its characters ("대 외 비")."""
    return r"\s*".join(re.escape(char) for char in word)


_KOREAN_WORDS = ("대외비", "사외비", "극비", "기밀", "보안문서", "비밀문서", "사내한정", "내부용")

_KOREAN = re.compile(
    "|".join([*(_spaced(word) for word in _KOREAN_WORDS), r"[123ⅠⅡⅢ]\s*급\s*비\s*밀"])
)

_ENGLISH = re.compile(
    r"\b(?:"
    + "|".join(
        r"\s+".join(words.split())
        for words in (
            "confidential",
            "top secret",
            "do not distribute",
            "company secret",
            "internal use only",
            "internal only",
            "for internal use",
        )
    )
    + r")\b",
    re.IGNORECASE,
)
""""Strictly Confidential" holds "Confidential", so it needs no line of its own."""


def head_of(text: str) -> list[str]:
    """The first ``HEAD_LINES`` lines of ``text`` that say anything."""
    head: list[str] = []
    for line in text.splitlines():
        if line.strip():
            head.append(line)
            if len(head) == HEAD_LINES:
                break
    return head


def marking_in(places: Iterable[str]) -> Marking | None:
    """The list the first marking found in ``places`` is on, or None."""
    for place in places:
        if _KOREAN.search(place):
            return "korean_marking"
        if _ENGLISH.search(place):
            return "english_marking"
    return None


def file_marking(file_name: str, text: str) -> Marking | None:
    """Whether a text file is marked: its name, then its head."""
    return marking_in([file_name.replace("_", " "), *head_of(text)])
