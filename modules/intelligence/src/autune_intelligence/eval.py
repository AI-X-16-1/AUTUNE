"""Module E's metric: calibration of the misalignment prediction.

    uv run --package autune-intelligence python -m autune_intelligence.eval

Reads labeled history (``history.labeled_examples``) and, per model version,
scores the probabilities that were actually stored and shown against whether a
decision from that meeting was reversed within the horizon. Also scores the
current in-process predictor refit-free over the same meetings, so a new
version can be compared before it ships.

Prints counts, rates and scores only — no meeting content, no team names.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from autune_core import session_scope

from .calibration import CalibrationReport, calibration_report
from .history import TRAINING_WINDOW, LabeledExample, labeled_examples
from .pipeline import get_misalignment_predictor

MIN_EXAMPLES = 30
"""Below this, a Brier score is noise — the report says so instead of printing one."""


def evaluate(examples: list[LabeledExample]) -> dict[str, CalibrationReport | None]:
    """``model_version -> report``; ``None`` for a version with too few examples."""
    by_version: dict[str, tuple[list[float], list[bool]]] = {}
    for e in examples:
        for version, p in e.stored_predictions.items():
            probs, outcomes = by_version.setdefault(f"stored:{version}", ([], []))
            probs.append(p)
            outcomes.append(e.reversed_within_horizon)

    predictor = get_misalignment_predictor()
    if examples:
        by_version[f"current:{predictor.model_version}"] = (
            predictor.predict([e.features for e in examples]),
            [e.reversed_within_horizon for e in examples],
        )

    return {
        version: calibration_report(probs, outcomes) if len(probs) >= MIN_EXAMPLES else None
        for version, (probs, outcomes) in sorted(by_version.items())
    }


def _format(reports: dict[str, CalibrationReport | None], labeled: int) -> str:
    if not reports:
        return (
            f"{labeled} labeled meetings. Nothing to calibrate yet: a meeting is labeled "
            "only once its 14-day horizon has passed."
        )
    lines = [f"{labeled} labeled meetings", ""]
    for version, report in reports.items():
        if report is None:
            lines.append(f"{version}: fewer than {MIN_EXAMPLES} examples, not scored")
            continue
        skill = f"{report.brier_skill:+.3f}" if report.brier_skill is not None else "n/a"
        lines += [
            f"{version}: n={report.count} base_rate={report.base_rate:.3f}",
            f"  brier={report.brier:.4f} (constant {report.brier_climatology:.4f},"
            f" skill {skill})  log_loss={report.log_loss:.4f}  ece={report.ece:.4f}",
            "  bin        n   predicted  observed",
        ]
        lines += [
            f"  {b.lower:.1f}-{b.upper:.1f}  {b.count:4d}   {b.mean_predicted:.3f}      "
            f"{b.observed_rate:.3f}"
            for b in report.bins
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autune_intelligence.eval")
    parser.add_argument(
        "--window-weeks",
        type=int,
        default=TRAINING_WINDOW.days // 7,
        help="How far back to read labeled meetings.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    with session_scope() as session:
        examples = labeled_examples(
            session, now=datetime.now(UTC), window=timedelta(weeks=args.window_weeks)
        )
    reports = evaluate(examples)

    if args.json:
        json.dump(
            {
                "labeled": len(examples),
                "reports": {v: asdict(r) if r else None for v, r in reports.items()},
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(_format(reports, len(examples)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
