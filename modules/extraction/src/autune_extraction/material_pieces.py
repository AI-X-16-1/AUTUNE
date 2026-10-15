"""How an upload's masked text is cut into the pieces that are embedded and
stored (#817, ADR 0011).

Everything here works on text that has been masked already
(``materials.store_upload`` masks the whole text first), and gives back
slices of that text and a heading path made of its own lines. Nothing is read
from the file again and nothing is invented, so a piece and its path carry no
value that the masker did not clear.

Two things come out of the cut:

* the **pieces**, which are stored and shown. Joined, they are the masked text
  again, so the same text cut the same way is the same pieces
  (``materials._kept_already``);
* for each piece, a **heading path** -- the lines of the headings above it,
  outermost first -- which is put in front of the piece in the text that is
  *embedded*, and in no stored column. It is why a sentence that names nothing
  ("이탈률은 3.2%로 집계됐다") still lands near a question that names the branch
  its heading did.

A heading is a markdown ``#`` line, or a short line that opens with a number
of the shape ``2.``, ``2.1`` or ``2.1.1`` (the shape of a report's outline,
which is all a text reader keeps of a .docx or a .pdf). Both are read from the
masked text; no heading style is taken from the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", " ")
"""Coarsest first. A piece is cut at the coarsest one that lets it fit, and
only then at a hard cut of the size. ``". "`` is a sentence end for Korean as
well (``다. ``, ``요. ``)."""

MAX_PATH_CHARS = 120
"""The longest heading path that is put before a piece. A longer one loses its
outermost headings first, so the nearest heading is the one that stays."""

_MARKDOWN = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,2})\.?[ \t]+\S")
"""``2.``, ``2.1``, ``2.1.1`` and a space and a word. Numbers of one or two
digits: a year or an amount at the start of a line ("2024 매출 100") is not a
heading."""

MAX_NUMBERED_HEADING_CHARS = 40
"""A numbered line longer than this is a sentence of a list, not a heading."""


@dataclass(frozen=True)
class Piece:
    text: str
    """What is stored and shown: a slice of the masked text."""
    path: str
    """The headings above it, ``" > "`` between, or empty."""

    @property
    def embedded(self) -> str:
        """What the embedder is given: the path, a line break, the piece."""
        return f"{self.path}\n{self.text}" if self.path else self.text


def heading_of(line: str) -> tuple[int, str] | None:
    """``(level, title)`` when ``line`` is a heading, else None."""
    stripped = line.strip()
    found = _MARKDOWN.match(stripped)
    if found:
        return len(found.group(1)), found.group(2).strip()
    found = _NUMBERED.match(stripped)
    if found and len(stripped) <= MAX_NUMBERED_HEADING_CHARS:
        return found.group(1).count(".") + 1, stripped
    return None


def _path_of(stack: list[tuple[int, str]]) -> str:
    titles = [title for _, title in stack]
    while len(" > ".join(titles)) > MAX_PATH_CHARS and len(titles) > 1:
        titles.pop(0)
    return " > ".join(titles)[:MAX_PATH_CHARS]


def sections(text: str) -> list[tuple[str, str]]:
    """``(heading path, section text)`` in order. A section starts at its
    heading line and runs to the next heading of any level; text before the
    first heading has an empty path. Joined, the sections are ``text``."""
    out: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    path, buffer = "", []
    for line in text.splitlines(keepends=True):
        found = heading_of(line)
        if found is None:
            buffer.append(line)
            continue
        if buffer:
            out.append((path, "".join(buffer)))
        level, title = found
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = _path_of(stack)
        buffer = [line]
    if buffer:
        out.append((path, "".join(buffer)))
    return out


def split_recursive(text: str, size: int, level: int = 0) -> list[str]:
    """Pieces of at most ``size`` characters, cut at the coarsest separator
    that lets them fit, no overlap; joined, they are ``text`` again."""
    if len(text) <= size:
        return [text] if text else []
    if level >= len(SEPARATORS):
        return [text[i : i + size] for i in range(0, len(text), size)]
    separator = SEPARATORS[level]
    parts = text.split(separator)
    pieces: list[str] = []
    current = ""
    for index, part in enumerate(parts):
        unit = part + (separator if index < len(parts) - 1 else "")
        if len(unit) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(split_recursive(unit, size, level + 1))
        elif len(current) + len(unit) <= size:
            current += unit
        else:
            if current:
                pieces.append(current)
            current = unit
    if current:
        pieces.append(current)
    return pieces


def pieces_of(masked: str, size: int, *, with_path: bool = True) -> list[Piece]:
    """``masked`` in pieces of at most ``size`` characters, each with the path
    of the headings above it (empty when ``with_path`` is off, or when the text
    has none). Joined, the pieces' ``text`` is ``masked``.

    **The boundaries do not depend on ``with_path``**: a piece never runs over
    a heading line, with the path or without it, so turning the path off
    changes what is embedded and nothing that is stored or compared
    (``materials._kept_already``).

    A run of white space that would stand alone as a piece joins the piece
    before it (or, at the start, the one after it): nothing is embedded for a
    blank, and the join stays exact. Such a piece is longer than ``size`` by
    white space only."""
    out: list[Piece] = []
    for found, body in sections(masked):
        path = found if with_path else ""
        for text in split_recursive(body, size):
            if text.strip():
                out.append(Piece(text, path))
            elif out:
                out[-1] = Piece(out[-1].text + text, out[-1].path)
            else:
                out.append(Piece(text, path))
    if out and not out[0].text.strip() and len(out) > 1:
        head = out.pop(0)
        out[0] = Piece(head.text + out[0].text, out[0].path)
    return out
