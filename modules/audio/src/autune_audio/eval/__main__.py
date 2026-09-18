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

RECALL_TARGET = 0.95


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

    differs: list[tuple[corpus.Row, str]] = []
    missed_categories: Counter[str] = Counter()
    caught = expected = correct = produced = 0

    for row in rows:
        ours = mask(row.text, recogniser=recogniser).text

        # **The exact string first.** Token recall counts a token as hidden when
        # it holds any `*` at all, so `010-****-56789` scores as masked and the
        # digit that leaked is invisible — which is the shape of the bug in #158
        # this harness exists to find. The corpus already carries what the
        # output should be; comparing against it is the strongest signal here
        # and the scores are what say *how far off* a row that differs is.
        if ours != row.masked:
            differs.append((row, ours))
            if row.is_positive:
                missed_categories.update(row.categories)

        if row.is_positive:
            recall = masking_recall(row.masked, ours)
            caught += recall.spans_we_caught
            expected += recall.spans_in_reference

        try:
            precision = masking_precision(row.masked, ours)
        except ValueError:
            # The masker changed the token count, so positions no longer line
            # up. That is a row we got wrong, not a reason to stop scoring the
            # other thirty-four.
            continue
        correct += precision.spans_that_should_be
        produced += precision.spans_we_masked

    recall_score = caught / expected if expected else 1.0
    precision_score = correct / produced if produced else 1.0
    exact = len(rows) - len(differs)

    print(
        f"corpus      {len(corpus.load())} rows - {len(corpus.positives())} positive, "
        f"{len(corpus.negatives())} negative"
    )
    print(f"recogniser  {'off' if args.no_recogniser else 'spoken_numbers'}")
    print()
    print(f"exact       {exact}/{len(rows)} rows match the corpus character for character")
    print(f"recall      {recall_score:.3f}   ({caught}/{expected} spans)   target {RECALL_TARGET}")
    print(f"precision   {precision_score:.3f}   ({correct}/{produced} spans masked correctly)")
    print()
    if differs:
        positives = [row for row, _ in differs if row.is_positive]
        negatives = [row for row, _ in differs if not row.is_positive]
        if positives:
            print(
                f"missed      {len(positives)} rows - "
                + ", ".join(
                    f"{category} {count}" for category, count in sorted(missed_categories.items())
                )
            )
        if negatives:
            print(f"over-masked {len(negatives)} rows")
    else:
        print("every row matches the corpus character for character")

    if args.verbose:
        for row, ours in differs:
            label = "MISSED" if row.is_positive else "OVER-MASKED"
            print(f"\n{label}  [{row.source}] {row.note or ''}")
            print(f"  expected  {row.masked}")
            print(f"  got       {ours}")

    # **A row that differs fails the run**, not only one that drags recall under
    # the target. A single digit left standing moves recall by nothing — the
    # token is still masked — and that is exactly the failure this corpus was
    # built after (#158). The recall target stays a gate too, for when the
    # corpus grows past what the masker can do and exact match stops being
    # reachable.
    #
    # Precision is reported and does not gate: a leaked national ID and an
    # over-masked date are not the same kind of wrong, and that trade is a
    # judgement the team makes with the number in front of it.
    return 0 if not differs and recall_score >= RECALL_TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
