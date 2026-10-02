"""How often do the number shapes the masker cannot read actually occur? (#160)

#160 lists ways a number can be read aloud that neither ``find_pii`` nor the
spoken-number recogniser catches -- groups split by commas, one syllable at a
time, a filler between groups. Each fix has a cost (a shared pattern widened,
or offset mapping in the recogniser), so the issue asks for a count first.

This counts those shapes in whatever transcripts are at hand and, for each
match, whether ``mask()`` already hides it. **It prints counts only, never
text**: the sources are transcripts, and a miss is by definition unmasked.

Sources, any combination:

    --postgres            every database on the local server with an
                          ``utterances`` table (dev, demo, test runs), deduplicated
    --hike                HiKE's human reference transcripts (1,121 utterances)
    --jsonl PATH          one JSON object per line with a ``text`` field

    uv run python modules/audio/scripts/count_number_shapes.py --postgres --hike
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

NUMERAL = "[0-9공영일이삼사오육칠팔구]"
"""A digit, or a Korean digit syllable as the recogniser reads one. 영 and 공
both mean zero; scale words (십 백 천 만) are not numerals here, as in
``recognition._DIGIT_SYLLABLES``."""

SHAPES: dict[str, re.Pattern[str]] = {
    # 공일공, 일이삼사, 오육칠팔 / 010, 1234, 5678
    "comma_groups": re.compile(rf"{NUMERAL}{{2,4}}(?:\s*,\s*{NUMERAL}{{2,4}}){{2,}}"),
    # 공 일 공 일 이 삼 사 ... / 0 1 0 1 2 3 4 ...
    "one_at_a_time": re.compile(rf"(?<!\S){NUMERAL}(?:\s+{NUMERAL}(?!\S)){{5,}}"),
    # 공일공 음 일이삼사 / 010 어 1234
    "filler_between": re.compile(rf"{NUMERAL}{{3,4}}\s+(?:음|어|아|그|저기|에)\s+{NUMERAL}{{3,4}}"),
}
LONG_RUN = re.compile(rf"{NUMERAL}(?:[\s,.\-]*{NUMERAL}){{6,}}")
"""The denominator: any run of seven or more numerals, separators allowed. Loose
on purpose -- it also counts ordinary words built from digit syllables -- so a
shape's share of it is an upper bound on how much of the number traffic it is."""


@dataclass
class Tally:
    utterances: int = 0
    long_runs: int = 0
    long_runs_masked: int = 0
    shapes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SHAPES, 0))
    shapes_masked: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SHAPES, 0))


def _masked(masked: str, start: int, end: int) -> bool:
    """Whether ``mask()`` touched the span: the masker preserves length, so the
    same offsets in its output either contain a mask character or do not."""
    return "*" in masked[start:end]


def tally(texts: Iterable[str]) -> Tally:
    from autune_audio.masking import mask  # noqa: PLC0415 - settings load on import
    from autune_audio.recognition import get_recogniser  # noqa: PLC0415

    recogniser = get_recogniser()
    out = Tally()
    for text in texts:
        out.utterances += 1
        masked: str | None = None

        def is_masked(start: int, end: int, text: str = text) -> bool:
            nonlocal masked
            if masked is None:
                masked = mask(text, recogniser=recogniser).text
            return len(masked) == len(text) and _masked(masked, start, end)

        for match in LONG_RUN.finditer(text):
            out.long_runs += 1
            out.long_runs_masked += is_masked(*match.span())
        for name, pattern in SHAPES.items():
            for match in pattern.finditer(text):
                out.shapes[name] += 1
                out.shapes_masked[name] += is_masked(*match.span())
    return out


def _databases() -> list[str]:
    listing = subprocess.run(
        [
            "docker",
            "exec",
            "autune-postgres-1",
            "psql",
            "-U",
            "autune",
            "-d",
            "postgres",
            "-tAc",
            "select datname from pg_database where datname like 'autune%'",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return sorted(listing)


def postgres_texts() -> Iterator[str]:
    """Every distinct stored utterance on the local server. Read with psql in a
    container, so no connection string or credential appears here."""
    seen: set[str] = set()
    for db in _databases():
        result = subprocess.run(
            [
                "docker",
                "exec",
                "autune-postgres-1",
                "psql",
                "-U",
                "autune",
                "-d",
                db,
                "-tAc",
                "select coalesce(json_agg(text), '[]') from utterances",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:  # no utterances table in this database
            continue
        for text in json.loads(result.stdout or "[]"):
            if text not in seen:
                seen.add(text)
                yield text


def hike_texts() -> Iterator[str]:
    from autune_audio.eval.hike import download, labels  # noqa: PLC0415

    for row in labels(download()):
        yield row.reference_raw


def jsonl_texts(path: Path) -> Iterator[str]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)["text"]


def report(name: str, t: Tally) -> str:
    lines = [
        f"## {name}",
        f"utterances           {t.utterances}",
        f"runs of 7+ numerals  {t.long_runs}  (masked {t.long_runs_masked})",
    ]
    for shape in SHAPES:
        lines.append(f"{shape:<20} {t.shapes[shape]}  (masked {t.shapes_masked[shape]})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--hike", action="store_true")
    parser.add_argument("--jsonl", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    sources: list[tuple[str, Iterable[str]]] = []
    if args.postgres:
        sources.append(("stored utterances (local Postgres, deduplicated)", postgres_texts()))
    if args.hike:
        sources.append(("HiKE references", hike_texts()))
    sources += [(str(path), jsonl_texts(path)) for path in args.jsonl]
    if not sources:
        parser.error("name at least one source")
    for name, texts in sources:
        print(report(name, tally(texts)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
