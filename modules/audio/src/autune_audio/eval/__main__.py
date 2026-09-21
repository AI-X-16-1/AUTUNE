"""Score the masker against the corpus. `python -m autune_audio.eval`

`modules/audio/CLAUDE.md` said this arrives with the corpus loader, once the
label format is known. It is known now: a row is text, what it should look like
masked, and where it came from.

Reports **both directions**, because the two failure modes pull against each
other and a release decision needs to see the trade rather than one side of it.
Recall is the number `docs/modules/audio.md` sets a target for; precision is the
number that says what the recall cost.

No GPU, no model, no network — the masker is patterns and a rule, so this runs
in a second and can sit in front of any change to either.

The scoring itself is `autune_audio.eval.score`, which the tests use too. This
file only argues, prints and exits.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from autune_audio.eval import corpus, score
from autune_audio.recognition import FakeRecogniser, SpokenNumberRecogniser


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autune_audio.eval", description=__doc__)
    parser.add_argument(
        "--no-recogniser",
        action="store_true",
        help="score the patterns alone — how a leak is attributed to one detector or the other",
    )
    parser.add_argument("--verbose", action="store_true", help="name every row that differs")
    args = parser.parse_args(argv)

    recogniser = FakeRecogniser() if args.no_recogniser else SpokenNumberRecogniser()
    rows = corpus.load()
    report = score.score(rows, recogniser=recogniser, declarations_apply=not args.no_recogniser)

    print(
        f"corpus      {report.rows} rows - {len(corpus.positives())} positive, "
        f"{len(corpus.negatives())} negative"
    )
    print(f"recogniser  {'off' if args.no_recogniser else 'spoken_numbers'}")
    print()
    print(f"exact       {report.exact}/{report.rows} rows match the corpus character for character")
    print(
        f"recall      {report.recall:.3f}   ({report.spans_caught}/{report.spans_expected} spans)"
        f"   target {score.RECALL_TARGET}"
    )
    print(
        f"precision   {report.precision:.3f}   "
        f"({report.spans_correct}/{report.spans_produced} spans masked correctly)"
    )
    print()

    positives = [row for row in report.unexplained if row.is_positive]
    negatives = [row for row in report.unexplained if not row.is_positive]
    if positives:
        print(
            f"missed      {len(positives)} rows - "
            + ", ".join(
                f"{category} {count}"
                for category, count in sorted(report.missed_categories.items())
            )
        )
    if negatives:
        print(f"over-masked {len(negatives)} rows")
    if report.declared:
        print(f"declared    {len(report.declared)} rows differ and say why (known_inexact)")
    if report.fixed:
        print(
            f"fixed       {len(report.fixed)} rows carry known_inexact and now match — "
            "remove the declaration"
        )
    if not report.unexplained and not report.declared and not report.fixed:
        print("every row matches the corpus character for character")

    # **Identification only.** Source, line and category are enough to open
    # the corpus file at the row. The text and the masked form are not printed,
    # in either direction: the corpus is invented values today and real
    # transcripts tomorrow, and a terminal is a log (privacy.md section 2).
    if args.verbose:
        for label, group in (
            ("UNEXPLAINED", report.unexplained),
            ("DECLARED", report.declared),
            ("FIXED", report.fixed),
        ):
            for row in group:
                reason = f"  {row.known_inexact}" if row.known_inexact else ""
                note = f"  ({row.note})" if row.note else ""
                print(f"{label:12s}{score.identify(row)}{reason}{note}")

    # **A row that differs without saying why fails the run**, not only one
    # that drags recall under the target. A single digit left standing moves
    # recall by nothing — the token is still masked — and that is exactly the
    # failure this corpus was built after (#158). A declared row does not
    # fail it; a declared row that has come right does, until the declaration
    # goes. The recall target stays a gate too, for when the corpus grows past
    # what the masker can do.
    #
    # Precision is reported and does not gate: a leaked national ID and an
    # over-masked date are not the same kind of wrong, and that trade is a
    # judgement the team makes with the number in front of it.
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
