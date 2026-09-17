"""``uv run --package autune-context --extra training python -m
autune_context.training train <output_dir>``
``uv run --package autune-context --extra training python -m
autune_context.training evaluate-test <model_dir>``

See ``train``'s docstring for what these need to actually run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from autune_context.training.train import TrainConfig, evaluate_test, train


def main() -> int:
    parser = argparse.ArgumentParser(prog="autune_context.training")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_p = subparsers.add_parser("train", help="fine-tune on KorNLI train, select on dev")
    train_p.add_argument("output_dir", type=Path)
    train_p.add_argument("--max-length", type=int, default=128)
    train_p.add_argument("--epochs", type=float, default=3.0)
    train_p.add_argument("--train-batch-size", type=int, default=32)
    train_p.add_argument("--eval-batch-size", type=int, default=64)
    train_p.add_argument("--learning-rate", type=float, default=2e-5)

    test_p = subparsers.add_parser(
        "evaluate-test", help="score a trained checkpoint on the held-out test split, once"
    )
    test_p.add_argument("model_dir", type=Path)
    test_p.add_argument("--max-length", type=int, default=128)
    test_p.add_argument("--eval-batch-size", type=int, default=64)

    args = parser.parse_args()

    if args.command == "train":
        metrics = train(
            TrainConfig(
                output_dir=args.output_dir,
                max_length=args.max_length,
                num_epochs=args.epochs,
                train_batch_size=args.train_batch_size,
                eval_batch_size=args.eval_batch_size,
                learning_rate=args.learning_rate,
            )
        )
        print(f"dev accuracy: {metrics.get('eval_accuracy')}")
    else:
        metrics = evaluate_test(
            args.model_dir, max_length=args.max_length, eval_batch_size=args.eval_batch_size
        )
        print(f"test accuracy: {metrics.get('eval_accuracy')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
