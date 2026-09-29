"""Module E's metric: calibration of the misalignment prediction.

    uv run --package autune-intelligence python -m autune_intelligence.eval

Reads labeled history (``history.labeled_examples``) and, per model version,
scores the probabilities that were actually stored and shown against whether a
decision from that meeting was reversed within the horizon. Also scores the
current in-process predictor, so a new version can be compared before it ships
— on held-out meetings when that predictor is fit from history, because an
in-sample score is not comparable with the stored ones (see ``HOLDOUT``).

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
from .pipeline import (
    get_misalignment_predictor,
    predictor_fits_from_history,
    reset_cache,
)

MIN_EXAMPLES = 30
"""Below this, a Brier score is noise — the report says so instead of printing one."""

HOLDOUT = timedelta(weeks=4)
"""How much of the recent past the fitted predictor is not allowed to learn from.

Only applies when the configured predictor is fit from history. Such a
predictor scored over the window it was fit on reports an **in-sample** number,
and `stored:` rows are out-of-sample, so comparing them decides nothing: fitting
200 trees on fifty-odd meetings beats the heuristic on its own training data
almost however bad the model is. Measured on random labels with no signal at
all, in-sample Brier skill came out +0.577 while the same model scored -0.084
on fresh data.

So the predictor is built as of ``now - HOLDOUT`` — it sees only meetings whose
horizon had closed by then — and scored only on meetings after that point. Four
weeks because it has to clear the 14-day label horizon with room left for
enough scored meetings; below the horizon nothing is scoreable at all."""


def _current_row(
    examples: list[LabeledExample], *, now: datetime, holdout: timedelta
) -> tuple[str, list[LabeledExample], object]:
    """The in-process predictor, the meetings it may be scored on, and its label.

    A predictor that is not fit from history has nothing to leak, so it is
    scored on everything. A fitted one is rebuilt as of the split and scored
    only after it — see ``HOLDOUT``.
    """
    if not predictor_fits_from_history():
        predictor = get_misalignment_predictor()
        return f"current:{predictor.model_version}", examples, predictor

    split = now - holdout
    reset_cache()
    try:
        predictor = get_misalignment_predictor(now=split)
    finally:
        # Do not leave a predictor fit at a past date in the process cache.
        reset_cache()
    scored = [e for e in examples if e.at > split]
    return f"current:{predictor.model_version} (out of sample)", scored, predictor


def evaluate(
    examples: list[LabeledExample],
    *,
    now: datetime | None = None,
    holdout: timedelta = HOLDOUT,
) -> dict[str, CalibrationReport | None]:
    """``model_version -> report``; ``None`` for a version with too few examples."""
    by_version: dict[str, tuple[list[float], list[bool]]] = {}
    for e in examples:
        for version, p in e.stored_predictions.items():
            probs, outcomes = by_version.setdefault(f"stored:{version}", ([], []))
            probs.append(p)
            outcomes.append(e.reversed_within_horizon)

    if examples:
        label, scored, predictor = _current_row(
            examples, now=now or datetime.now(UTC), holdout=holdout
        )
        if scored:
            by_version[label] = (
                predictor.predict([e.features for e in scored]),  # type: ignore[attr-defined]
                [e.reversed_within_horizon for e in scored],
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
    parser.add_argument(
        "--holdout-weeks",
        type=int,
        default=HOLDOUT.days // 7,
        help="Recent weeks a fitted predictor may not learn from, and is scored on.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    with session_scope() as session:
        examples = labeled_examples(
            session, now=datetime.now(UTC), window=timedelta(weeks=args.window_weeks)
        )

    now = datetime.now(UTC)
    reports = evaluate(examples, now=now, holdout=timedelta(weeks=args.holdout_weeks))

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
