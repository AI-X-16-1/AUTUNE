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
"""

from __future__ import annotations

import argparse
from collections import Counter

from autune_audio.eval import corpus
from autune_audio.eval.metrics import masking_precision, masking_recall
from autune_audio.masking import mask
from autune_audio.recognition import FakeRecogniser, SpokenNumberRecogniser


def main() -> int:
    parser = argparse.ArgumentParser(prog="autune_audio.eval", description=__doc__)
    parser.add_argument(
        "--no-recogniser",
        action="store_true",
        help="score the patterns alone — how a leak is attributed to one detector or the other",
    )
    parser.add_argument("--verbose", action="store_true", help="print every row that fails")
    args = parser.parse_args()

    recogniser = FakeRecogniser() if args.no_recogniser else SpokenNumberRecogniser()
    rows = corpus.load()

    missed: list[corpus.Row] = []
    over: list[corpus.Row] = []
    missed_categories: Counter[str] = Counter()
    caught = expected = correct = produced = 0

    for row in rows:
        ours = mask(row.text, recogniser=recogniser).text

        if row.is_positive:
            recall = masking_recall(row.masked, ours)
            caught += recall.spans_we_caught
            expected += recall.spans_in_reference
            if recall.recall < 1.0:
                missed.append(row)
                missed_categories.update(row.categories)

        precision = masking_precision(row.masked, ours)
        correct += precision.spans_that_should_be
        produced += precision.spans_we_masked
        if precision.precision < 1.0:
            over.append(row)

    recall_score = caught / expected if expected else 1.0
    precision_score = correct / produced if produced else 1.0

    print(
        f"corpus      {len(rows)} rows — {len(corpus.positives())} positive, "
        f"{len(corpus.negatives())} negative"
    )
    print(f"recogniser  {'off' if args.no_recogniser else 'spoken_numbers'}")
    print()
    print(f"recall      {recall_score:.3f}   ({caught}/{expected} spans)   target 0.95")
    print(f"precision   {precision_score:.3f}   ({correct}/{produced} spans masked correctly)")
    print()
    if missed:
        print(
            f"missed      {len(missed)} rows — "
            + ", ".join(
                f"{category} {count}" for category, count in sorted(missed_categories.items())
            )
        )
    if over:
        print(f"over-masked {len(over)} rows")
    if not missed and not over:
        print("every row scored exactly as the corpus says it should")

    if args.verbose:
        for label, group in (("MISSED", missed), ("OVER-MASKED", over)):
            for row in group:
                ours = mask(row.text, recogniser=recogniser).text
                print(f"\n{label}  [{row.source}] {row.note or ''}")
                print(f"  expected  {row.masked}")
                print(f"  got       {ours}")

    # Recall is the gate. Precision is reported and does not fail the run: the
    # trade is a judgement the team makes with the number in front of it, not
    # one this script makes by exiting non-zero.
    return 0 if recall_score >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
