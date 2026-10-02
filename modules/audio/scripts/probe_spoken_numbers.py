"""Read made-up numbers aloud in each style #160 names, and see what survives masking.

#160's shapes only matter if Whisper actually writes them: a number read
"공일공, 일이삼사, 오육칠팔" may come back as ``010-1234-5678`` (which the patterns
catch), as syllables with commas (which they do not), or as something else.
No transcript at hand contains enough numbers read aloud to count
(``count_number_shapes.py``), so this makes some.

For each kind (phone, account, resident number), style and rate it speaks a
sentence with macOS ``say``, runs the stored path's ``transcribe()`` on it, masks
the result the way the pipeline does, and counts how many of the number's
digits are still readable afterwards. A clip **leaks** when more of them are
readable than when the same number is written as digits (``010-1234-5678``)
and masked -- the masker keeps a prefix and the last four by design, so the
baseline is what it keeps, not zero. The numbers are generated, never real,
so printing the transcripts is safe; the audio is written to a temporary
directory and deleted.

    uv run python modules/audio/scripts/probe_spoken_numbers.py --per-kind 2

Synthetic speech is not a meeting: TTS reads evenly, never mumbles, and pauses
exactly where it is told. What this measures is how the model *writes* each
style, not how often people speak it.
"""

from __future__ import annotations

import argparse
import random
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

SYLLABLE = dict(zip("0123456789", "공일이삼사오육칠팔구", strict=True))
DIGIT_OF = {v: k for k, v in SYLLABLE.items()} | {"영": "0"}
KINDS = {
    "phone": ((3, 4, 4), "제 번호는 {} 입니다."),
    "account": ((3, 2, 6), "계좌는 {} 이에요."),
    "rrn": ((6, 7), "주민번호는 {} 입니다."),
}
STYLES = ("grouped", "comma", "one_at_a_time", "filler")
VOICE = "Yuna"
"""The one Korean voice on a stock macOS that actually speaks; the others render
an empty file until downloaded."""
RATES = (150, 220)
"""Words per minute: slow and brisk, standing in for a second speaker."""


def make_number(kind: str, rng: random.Random) -> list[str]:
    groups, _ = KINDS[kind]
    digits = ["".join(rng.choice("0123456789") for _ in range(n)) for n in groups]
    if kind == "phone":
        digits[0] = "010"
    if kind == "rrn":
        digits[0] = f"{rng.randint(70, 99)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    return digits


def spoken(groups: list[str], style: str) -> str:
    words = ["".join(SYLLABLE[d] for d in g) for g in groups]
    if style == "grouped":
        return " ".join(words)
    if style == "comma":
        return ", ".join(words)
    if style == "filler":
        return " 음 ".join(words)
    # One syllable at a time: a short silence after each, a longer one between groups.
    return " [[slnc 400]] ".join(" [[slnc 150]] ".join(w) for w in words)


def readable_digits(masked: str, number: str) -> int:
    """How many of ``number``'s digits can still be read, in order, in ``masked``,
    counting a digit syllable as its digit. Longest common subsequence."""
    seen = "".join(DIGIT_OF.get(c, c) for c in masked if c.isdigit() or c in DIGIT_OF)
    a, b = seen, number
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b):
            cur.append(prev[j] + 1 if ca == cb else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def classify(text: str) -> str:
    if re.search(r"\d", text) and not re.search(f"[{''.join(DIGIT_OF)}]{{3}}", text):
        return "digits"
    if re.search(r"\d", text):
        return "mixed"
    return "syllables"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-kind", type=int, default=2)
    parser.add_argument("--seed", type=int, default=160)
    parser.add_argument("--quiet", action="store_true", help="summary only")
    args = parser.parse_args(argv)

    from autune_audio.decoding import decode  # noqa: PLC0415
    from autune_audio.masking import mask  # noqa: PLC0415
    from autune_audio.pipeline import transcribe  # noqa: PLC0415
    from autune_audio.recognition import get_recogniser  # noqa: PLC0415

    rng = random.Random(args.seed)
    recogniser = get_recogniser()
    leaks: dict[tuple[str, str], int] = defaultdict(int)
    silent: dict[tuple[str, str], int] = defaultdict(int)
    totals: dict[tuple[str, str], int] = defaultdict(int)
    written: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))

    with tempfile.TemporaryDirectory() as tmp:
        for kind, (_, sentence) in KINDS.items():
            for _ in range(args.per_kind):
                groups = make_number(kind, rng)
                number = "".join(groups)
                written_as_digits = sentence.format("-".join(groups))
                baseline = readable_digits(
                    mask(written_as_digits, recogniser=recogniser).text, number
                )
                for style in STYLES:
                    for rate in RATES:
                        path = Path(tmp) / "clip.aiff"
                        subprocess.run(
                            [
                                "say",
                                "-v",
                                VOICE,
                                "-r",
                                str(rate),
                                "-o",
                                str(path),
                                sentence.format(spoken(groups, style)),
                            ],
                            check=True,
                        )
                        text = " ".join(
                            s.text.strip() for s in transcribe(decode(path)).segments
                        ).strip()
                        path.unlink()
                        masked = mask(text, recogniser=recogniser).text
                        left = readable_digits(masked, number)
                        key = (kind, style)
                        totals[key] += 1
                        leaked = left > baseline
                        leaks[key] += leaked
                        silent[key] += not text
                        written[key][classify(text) if text else "empty"] += 1
                        if not args.quiet:
                            flag = "LEAK" if leaked else "ok  "
                            print(f"{flag} {kind:<8}{style:<14}{rate:<4} {text!r} -> {masked!r}")

    print()
    print(f"{'kind':<9}{'style':<15}{'clips':>5}{'leaked':>8}   how Whisper wrote it")
    for kind in KINDS:
        for style in STYLES:
            key = (kind, style)
            shapes = ", ".join(f"{k} {v}" for k, v in sorted(written[key].items()))
            print(f"{kind:<9}{style:<15}{totals[key]:>5}{leaks[key]:>8}   {shapes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
