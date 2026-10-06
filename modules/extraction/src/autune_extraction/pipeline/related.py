"""Which other lines of a meeting are about the same thing as one line.

An action item's sentence is often a pointer -- "그건 다음 빌드에 넣을게요" -- and
what it points at was said earlier, sometimes many turns earlier and not next to
it. The lines just before it are the resolver's window; this finds the ones that
are *about the same thing* wherever they sit in the meeting, so the summary of
an item can draw on them and the screen can show them beneath it.

**Word overlap, weighted by rarity; no model.** Each line becomes the two-letter
pieces of its words (not its last word, the predicate), weighted by how few lines
of the meeting contain them, and two lines are as similar as the angle between
those weightings. Deterministic and local.

**This is a wide net, not an answer.** Measured on two dummy meetings it put a
relevant line in the top three about one time in three -- "제가" and "그건" are in
half the meeting -- so what it finds is only *offered* to the model, which says
which lines it actually used, and only those are stored and shown. Showing these
directly would put noise in front of the person checking an item.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Collection, Sequence

_WORD = re.compile(r"[가-힣A-Za-z0-9]+")

MIN_LENGTH = 8
"""A line shorter than this (after stripping) is an answer or a filler -- "네 좋아요",
"그렇게 하죠" -- and says too little to be about anything."""

MIN_SCORE = 0.1
"""Below this the overlap is a shared common word, not a shared subject. Low on
purpose: the net is wide and the model chooses, and the rarity weighting already
makes a line that shares one rare word score well under a line that shares
several."""

MAX_RELATED = 8
"""Candidates offered to the model, not lines shown to a person."""


def _pieces(text: str) -> Counter[str]:
    """Two-letter pieces of the line's words, leaving out its last word.

    The last word of a Korean sentence is the predicate -- "드릴게요", "넣어 보려고요"
    -- and two lines that both end in a promise are not about the same thing. What
    a line is about sits in the words before it. A one-word line keeps its word.
    """
    pieces: Counter[str] = Counter()
    words = _WORD.findall(text.lower())
    for word in words[:-1] if len(words) > 1 else words:
        if len(word) < 2:
            continue
        for i in range(len(word) - 1):
            pieces[word[i : i + 2]] += 1
    return pieces


def _stems(text: str) -> set[str]:
    """The first two letters of each word of a line: what the word is, without
    the particle or the ending Korean puts after it ("견적은", "견적을")."""
    return {word[:2] for word in _WORD.findall(text.lower()) if len(word) >= 2}


def drawn_on(summary: str, target: str, before: Sequence[tuple[str, str]]) -> list[str]:
    """Which of the lines said just before ``target`` a summary of it took a
    word from: the ids of those ``(id, text)`` lines that hold a word the
    summary has and the target does not.

    A summary written with the label (``llm.usable_summary``) does not say
    what it drew on, and without that nothing was stored for the screen's
    "요약에 쓴 발화". This says it without asking a model anything: the summary
    of "그건 제가 수요일까지 받아서 올릴게요" that reads "서버 비용 견적을
    수요일까지 받아서 올린다" took 서버, 비용 and 견적 from the line before,
    and that line is the one a person checking the summary needs to see.

    Not ``related_ids``'s wide net: a word the target already has cites
    nothing, so a line that merely shares "제가" with the summary is not here.
    What it can still do is cite a line for a word the model would have
    written anyway; the lines are few (the summary's own context) and each is
    one the summary was allowed to draw on.
    """
    added = _stems(summary) - _stems(target)
    return [line_id for line_id, text in before if added & _stems(text)]


def related_ids(
    target_id: str,
    lines: Sequence[tuple[str, str]],
    *,
    exclude: Collection[str] = (),
    limit: int = MAX_RELATED,
    min_score: float = MIN_SCORE,
) -> list[str]:
    """The ids of the lines most like ``target_id``'s, the most alike first.

    Best first because a request that has to shrink to fit the outbound limit
    drops candidates from the end (``resolver._fitted``); the reader is shown
    only the lines the model cites, in spoken order, from the table.

    ``lines`` is the meeting in spoken order as ``(id, text)``, blank lines and
    non-consenting speakers' lines already removed by the caller -- the same
    filtered sequence every other read of the transcript uses. ``exclude`` are
    lines to leave out whatever their score (the target itself is always left
    out): the ones the reader is already being shown.
    """
    texts = dict(lines)
    target = texts.get(target_id)
    if target is None:
        return []
    pieces = {line_id: _pieces(text) for line_id, text in lines}
    frequency: Counter[str] = Counter()
    for counted in pieces.values():
        frequency.update(counted.keys())
    total = len(pieces)

    def weigh(counted: Counter[str]) -> dict[str, float]:
        return {p: n * math.log(total / frequency[p]) for p, n in counted.items() if frequency[p]}

    wanted = weigh(pieces[target_id])
    norm = math.sqrt(sum(v * v for v in wanted.values()))
    if not norm:
        return []

    skip = {target_id, *exclude}
    scored: list[tuple[float, int, str]] = []
    for position, (line_id, text) in enumerate(lines):
        if line_id in skip or len(text.strip()) < MIN_LENGTH:
            continue
        other = weigh(pieces[line_id])
        other_norm = math.sqrt(sum(v * v for v in other.values()))
        if not other_norm:
            continue
        dot = sum(v * other.get(p, 0.0) for p, v in wanted.items())
        score = dot / (norm * other_norm)
        if score >= min_score:
            scored.append((score, position, line_id))
    best = sorted(scored, key=lambda s: (-s[0], s[1]))[:limit]
    return [line_id for _, _, line_id in best]
