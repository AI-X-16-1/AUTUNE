"""The masking evaluation corpus: text, what it should look like masked, and where it came from.

A file rather than a list inside a test, because a test can only answer pass or
fail and the question here is *how much*. `docs/modules/audio.md` puts masking
recall at 0.95 and then 0.99; nothing in the repository could produce either
number until this existed.

**Both directions, in one file.** A row whose ``masked`` equals its ``text`` is
an example of something that must be left alone, and it is scored the same way
as one that must be hidden. That shape is deliberate: the masker's two failure
modes pull against each other, and every widening in this module so far has
bought recall with precision without the cost being visible until someone ran
real text (#125, #148, #158). A corpus that only holds personal data can only
measure one of the two.

``source`` says where a row came from and is not decoration:

- ``planted`` — a value written for this corpus. Every number in one is
  invented; none belongs to anybody.
- ``transcript`` — a line the evaluation recording actually produced, quoted
  from `docs/modules/audio-evaluations/`. These are the rows worth trusting
  most, because nobody chose them to make a point.
- ``written`` — a line written to stand for a shape the recording did not
  happen to contain.

**The transcript rows are the thin part.** The evaluation recording produced 123
segments and seven of them are quoted in the reports; the rest are not in the
repository, because the audio is deleted and the transcript was never committed.
Growing this file means running the corpus over a transcript, not inventing more
rows — a corpus of things somebody thought of is a corpus that scores well on
things somebody thought of, which is exactly how an earlier version of it
reported 1.000 while every Korean particle went through untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

Source = Literal["planted", "transcript", "written"]

CORPUS_DIR = "fixtures"
CORPUS_FILE = "masking.jsonl"
"""`fixtures/`, not `corpus/`.

The repository ignores `corpus/` everywhere, and the reason is in `.gitignore`:
downloaded training corpora are large, carry their own licences, and GitHub
refuses files over 100MB. This is the opposite of that — thirty-five lines we
wrote, whose whole point is to be versioned alongside the patterns they score.
The rule is right; the name collided with it. `packages/contracts` calls its own
small committed data `fixtures/`, so this does too."""


@dataclass(frozen=True)
class Row:
    """One line of the corpus.

    ``masked`` is what the masker should produce. When it equals ``text`` the
    row is a negative: nothing in it is personal data and touching it is the
    failure.
    """

    text: str
    masked: str
    source: Source
    categories: tuple[str, ...]
    note: str | None = None

    @property
    def is_positive(self) -> bool:
        return self.masked != self.text

    def __repr__(self) -> str:
        """Text only, never the masked form, so a log line cannot carry both.

        The pair is what makes a value recoverable — one side shows the shape and
        the other shows what was under it. The corpus holds invented values, but
        this object is one `print` away from holding a real one.
        """
        kind = "positive" if self.is_positive else "negative"
        return f"Row({kind}, source={self.source!r}, categories={list(self.categories)})"


@lru_cache(maxsize=1)
def load() -> tuple[Row, ...]:
    """Every row, in file order. Cached — the file does not change at runtime."""
    raw = resources.files(__package__).joinpath(CORPUS_DIR, CORPUS_FILE).read_text("utf-8")
    rows = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{CORPUS_FILE}:{number} is not JSON: {exc}") from exc
        rows.append(
            Row(
                text=entry["text"],
                masked=entry["masked"],
                source=entry["source"],
                categories=tuple(entry.get("categories", ())),
                note=entry.get("note"),
            )
        )
    if not rows:
        raise ValueError(f"{CORPUS_FILE} is empty")
    return tuple(rows)


def positives() -> tuple[Row, ...]:
    """Rows holding personal data. Recall is measured over these."""
    return tuple(row for row in load() if row.is_positive)


def negatives() -> tuple[Row, ...]:
    """Rows holding none. Precision is measured over all rows, but these are
    where over-masking shows up on its own, without a real span nearby to hide
    behind."""
    return tuple(row for row in load() if not row.is_positive)
