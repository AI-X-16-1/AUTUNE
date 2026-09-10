"""Fine-tune the utterance classifier on a prepared dataset.

    uv run --package autune-extraction --extra training \
        python -m autune_extraction.training \
        --data dataset/ami --out runs/kf-deberta-v1

``--data`` is a directory holding ``train.jsonl`` and ``validation.jsonl`` as
``autune_extraction.labeling`` writes them. ``--out`` receives the checkpoint,
the tokenizer, and ``run.json`` -- the config, the label order, the per-split
counts and file fingerprints, and the final metrics.

Prints the label counts before starting. A collapsed class is worth seeing
before an hour of training rather than after it, and it is the failure that
looks most like success in the loss curve.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dataset import LABELS, DatasetError, label_counts, read_split
from .train import BASE_CHECKPOINT, RunConfig, train


def main() -> None:
    parser = argparse.ArgumentParser(prog="autune_extraction.training", description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="directory of split JSONL files")
    parser.add_argument("--out", type=Path, required=True, help="where to write the checkpoint")
    parser.add_argument(
        "--base", default=BASE_CHECKPOINT, help=f"encoder (default {BASE_CHECKPOINT})"
    )
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument(
        "--no-class-weights",
        action="store_true",
        help="train with a flat loss; see dataset.class_weights for why the default is not",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the splits, print the counts, and stop. Needs no model.",
    )
    args = parser.parse_args()

    try:
        splits = {name: read_split(args.data, name) for name in ("train", "validation")}
    except DatasetError as exc:
        raise SystemExit(str(exc)) from exc

    for name, examples in splits.items():
        counts = label_counts(examples)
        print(f"{name:<12} {len(examples):>7,}", file=sys.stderr)
        for label in LABELS:
            share = counts[label] / len(examples)
            mark = "  <- empty" if counts[label] == 0 else ""
            print(f"  {label:<16}{counts[label]:>7,}  {share:>6.1%}{mark}", file=sys.stderr)

    if args.dry_run:
        return

    config = RunConfig(
        base_checkpoint=args.base,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weighted_loss=not args.no_class_weights,
    )
    out = train(args.data, args.out, config)
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
