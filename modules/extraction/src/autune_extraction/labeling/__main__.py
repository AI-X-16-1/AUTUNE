"""Build a labelled dataset from an annotated corpus.

    uv run --package autune-extraction python -m autune_extraction.labeling \
        --corpus dataset/ami_public_manual_1.6.2 \
        --out dataset/ami

Writes ``train.jsonl``, ``validation.jsonl`` and ``test.jsonl`` in the format the
evaluation harness reads, and prints a summary to stderr so the counts can be
read without opening the files.

Utterances that are none of the kinds are written as ``none`` (#149): sampled
into train at ``--none-ratio`` and into validation one to one. ``test.jsonl`` is
the natural distribution -- every act of the test meetings that carry the
decision layer -- and ``test_closed.jsonl`` is the labelled-only test split, for
comparison with scores taken before ``none`` existed. See ``corpus.add_none``.

The summary is the point of running this more than once: a class that collapses
between corpus versions, or a split that lands lopsided, is visible here and
invisible once training starts.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from autune_extraction.labels import NONE

from .corpus import SPLITS, AmiReader, add_none, split_by_meeting, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(prog="autune_extraction.labeling", description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True, help="AMI corpus root")
    parser.add_argument("--out", type=Path, required=True, help="directory to write into")
    parser.add_argument(
        "--min-words",
        type=int,
        default=1,
        help="drop acts that resolve to fewer words than this (default 1)",
    )
    parser.add_argument(
        "--none-ratio",
        type=float,
        default=1.0,
        help=(
            "none rows in train per labelled row (default 1.0, the setting #149 "
            "measured). 0 writes no none rows to train, and the training loop "
            "refuses a split with a class missing"
        ),
    )
    args = parser.parse_args()
    if args.none_ratio < 0:
        parser.error("--none-ratio must be zero or more")

    reader = AmiReader(args.corpus)
    try:
        reader.check_mapping()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    rows = list(reader.load(min_words=args.min_words, include_none=True))
    examples = [row for row in rows if row.kind != NONE]
    if not examples:
        raise SystemExit(f"no labelled utterances found under {args.corpus}")

    labelled = split_by_meeting(examples)
    splits = add_none(
        labelled,
        [row for row in rows if row.kind == NONE],
        annotated=reader.decision_meetings(),
        ratio=args.none_ratio,
    )
    for name in SPLITS:
        write_jsonl(args.out / f"{name}.jsonl", splits[name])
    # The test split as it was before none existed, so a new score can be put
    # next to an old one. Not the metric -- test.jsonl is.
    write_jsonl(args.out / "test_closed.jsonl", labelled["test"])

    _report(splits)
    print(
        f"  test_closed {len(labelled['test']):>6}  labelled only, all test meetings",
        file=sys.stderr,
    )


def _report(splits: dict[str, list]) -> None:
    """Counts to stderr, so stdout stays free for a caller that wants the files.

    Never a sample utterance. The corpus is public, but this is the same code
    path a Korean set will run through, and a summary that prints meeting text is
    the leak invariant 11 forbids arriving through a convenience.
    """
    total = sum(len(rows) for rows in splits.values())
    print(f"{total} utterances, {NONE} included", file=sys.stderr)
    for name in SPLITS:
        rows = splits[name]
        meetings = len({row.meeting for row in rows})
        share = len(rows) / total if total else 0.0
        print(
            f"  {name:<11} {len(rows):>6}  ({share:.1%})  {meetings:>3} meetings", file=sys.stderr
        )

        by_kind = Counter(row.kind for row in rows)
        for kind, count in by_kind.most_common():
            print(
                f"      {kind:<14} {count:>6}  {count / max(len(rows), 1):>6.1%}", file=sys.stderr
            )
        if not by_kind:
            print("      (empty)", file=sys.stderr)

    _warn_on_drift(splits)


def _warn_on_drift(splits: dict[str, list], *, limit: float = 0.1) -> None:
    """Say so when a class is a different share of one split than of another.

    The splits are balanced by meeting, and a meeting is not divisible: with
    seventeen of them in a held-out split there is a floor on how even this can
    get. Printing the gap is what keeps it from being discovered as a surprising
    validation score months later.
    """
    # ``none`` differs between splits by construction -- sampled into train and
    # validation, all of it into test -- so it is left out of the comparison
    # entirely: not as a class, and not in the denominator, where it would make
    # every kind look rarer in test than it is among labelled rows.
    labelled = {name: [row for row in rows if row.kind != NONE] for name, rows in splits.items()}
    kinds = {row.kind for rows in labelled.values() for row in rows}
    for kind in sorted(kinds):
        shares = {
            name: sum(1 for row in rows if row.kind == kind) / max(len(rows), 1)
            for name, rows in labelled.items()
        }
        spread = max(shares.values()) - min(shares.values())
        if spread > limit:
            detail = "  ".join(f"{name} {share:.1%}" for name, share in shares.items())
            print(
                f"  note: {kind} differs by {spread:.1%} between splits ({detail})",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
