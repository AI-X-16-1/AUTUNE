"""The part of an utterance an item or a decision was made from.

One person talking for a minute is one utterance. The classifier reads such a
turn sentence by sentence (``pipeline.llm.said_lines``) and a promise or a
decision in it becomes a row of its own, made from that sentence
(``decisions.in_pieces``) -- but the row cites the utterance, and a reader who
opened it was shown the whole minute to find the one sentence in (the user,
2026-10-08: show only the part of the utterance the item is about).

So the part is kept, as **where it is and not what it says**: two offsets into
the text module A stored, on the row that already links the item to the
utterance. No word is copied. What a person is shown is cut from the stored,
masked text when they ask (``cut``), so it is what was said and nothing a model
wrote; when the utterance is deleted there is nothing left to cut, and when it
is corrected the offsets are dropped (``service.apply_source_corrections``)
because they were counted on the text before the correction.
"""

from __future__ import annotations

from collections.abc import Iterable

Span = tuple[int, int]
"""``(start, end)`` in characters, ``text[start:end]`` being the part."""


def _compact(text: str) -> tuple[str, list[int]]:
    """``text`` without its whitespace, and where each kept character was."""
    kept = [(char, at) for at, char in enumerate(text) if not char.isspace()]
    return "".join(char for char, _ in kept), [at for _, at in kept]


def span_of(whole: str, parts: Iterable[str]) -> Span | None:
    """Where ``parts`` sit in ``whole``: from the first character of the earliest
    to the last character of the latest, or ``None`` when there is no part to
    show -- a part that is not in ``whole``, or parts that together are all of it.

    Whitespace is not compared. A long turn is cut at whitespace and a short
    sentence is joined to the next with one space, so a part has the turn's
    characters in the turn's order and not always its spacing.

    Several parts are one span, whatever lies between them: a decision settled
    over two sentences of a turn is quoted from the first to the second.
    """
    text, places = _compact(whole)
    if not text:
        return None
    first: int | None = None
    last: int | None = None
    for part in parts:
        wanted, _ = _compact(part)
        if not wanted:
            continue
        at = text.find(wanted)
        if at < 0:
            return None
        first = at if first is None else min(first, at)
        last = at + len(wanted) if last is None else max(last, at + len(wanted))
    if first is None or last is None or (first == 0 and last == len(text)):
        return None
    return places[first], places[last - 1] + 1


def cut(text: str, start: int | None, end: int | None) -> str | None:
    """``text[start:end]``, or ``None`` when no part is recorded, the offsets do
    not fit this text, or the part is all of it. Nothing is added to what was
    said, an ellipsis included: where the part stops is the reader's to see from
    the whole utterance beside it."""
    if start is None or end is None or not 0 <= start < end <= len(text):
        return None
    part = text[start:end].strip()
    if not part or part == text.strip():
        return None
    return part
