"""``python -m autune_extraction.eval`` - score a run against the evaluation set.

Takes a predictions file rather than loading a model. The classifier does not
exist yet (#10), and when it does, scoring stays separable from serving: a run
that has already been produced can be rescored without a GPU, and a model that
changes does not change what the score means.

Prints no utterance text. See ``dataset`` for why.

Output is ASCII. The team runs Korean Windows, where the console encoding is
cp949 and a printed em dash raises UnicodeEncodeError -- a harness that dies
on its own report is worse than a plain-looking one. ``main`` also puts stdout
into UTF-8 so a path with Korean characters in it cannot do the same.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from .dataset import EvalSetError, load_eval_set, load_predictions
from .metrics import AMI_BEST_PUBLISHED_ACTION_ITEM_F1, Report, action_item_f1, score


def format_report(report: Report, eval_set_fingerprint: str) -> str:
    lines = [
        f"Evaluation set {eval_set_fingerprint} - {report.n} utterances",
        "",
        f"{'kind':<16}{'P':>8}{'R':>8}{'F1':>8}{'support':>10}{'predicted':>11}",
    ]
    for class_score in report.per_class:
        lines.append(
            f"{class_score.kind.value:<16}"
            f"{class_score.precision:>8.3f}{class_score.recall:>8.3f}{class_score.f1:>8.3f}"
            f"{class_score.support:>10}{class_score.predicted:>11}"
        )

    derived = action_item_f1(report)
    lines += [
        "",
        f"macro F1, five-way   {report.macro_f1:.4f}   <- the metric (ADR 0006)",
        f"accuracy             {report.accuracy:.4f}",
        "",
        f"action item F1       {derived:.4f}   derived from the commitment class",
        f"best published (AMI) {AMI_BEST_PUBLISHED_ACTION_ITEM_F1:.4f}   Liu et al., ICASSP 2023",
    ]

    if report.absent_kinds:
        absent = ", ".join(k.value for k in report.absent_kinds)
        lines += [
            "",
            f"note: {absent} appears in neither the gold set nor the output. Its F1 is",
            "      0.0 by convention and it is pulling the macro average down for a",
            "      reason that is about the evaluation set, not the model.",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="autune_extraction.eval", description=__doc__)
    parser.add_argument("--eval-set", type=Path, required=True, help="held-out JSONL, gitignored")
    parser.add_argument("--predictions", type=Path, required=True, help="a run's JSONL output")
    args = parser.parse_args(argv)

    try:
        eval_set = load_eval_set(args.eval_set)
        predicted = load_predictions(args.predictions, eval_set)
    except EvalSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_report(score(eval_set.labels, predicted), eval_set.fingerprint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
