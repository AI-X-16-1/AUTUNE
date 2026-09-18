"""``uv run --package autune-gap python -m autune_gap.eval`` — score gap
detection against the labeled set.

Output is ASCII. The team runs Korean Windows, where the console encoding is
cp949 and a printed em dash raises UnicodeEncodeError -- a harness that dies on
its own report is worse than a plain-looking one. ``main`` also puts stdout into
UTF-8, because the item keys are ASCII but a dataset path need not be.

Prints template item keys, counts and case ids. No topic label and no line of a
transcript: a label is transcript text, and this report gets pasted into issues.
"""

from __future__ import annotations

import argparse
import io
import sys

from autune_gap.config import get_settings
from autune_gap.eval.dataset import DEFAULT_DATASET, EvalSetError
from autune_gap.eval.metrics import (
    EXTRACTION,
    KEYWORD,
    NO_NOUN,
    PARTIAL,
    TARGET_PRECISION_SIX_WEEKS,
    TARGET_PRECISION_THREE_MONTHS,
    Report,
)
from autune_gap.eval.runner import run_all


def format_report(report: Report, *, extractor: str) -> str:
    lines = [
        f"Gap detection -- {len(report.cases)} cases, extractor {extractor}",
        "",
        f"{'case':<24}{'template':<18}{'topics':>7}{'real':>6}{'high':>6}{'TP':>5}{'FP':>5}",
    ]
    for case in report.cases:
        lines.append(
            f"{case.case_id:<24}{case.template_key:<18}{case.topics:>7}"
            f"{len(case.real):>6}{len(case.raised_high):>6}"
            f"{len(case.true_positives):>5}{len(case.false_positives):>5}"
        )

    lines += ["", _headline(report), ""]

    for case in report.cases:
        if case.false_positives:
            named = [f"{item} ({case.cause(item)})" for item in sorted(case.false_positives)]
            lines.append(f"  false positive  {case.case_id}: {named}")
    for case in report.cases:
        if case.missed:
            lines.append(f"  missed          {case.case_id}: {sorted(case.missed)}")

    by_cause = report.false_positives_by_cause
    if by_cause:
        lines += ["", "  false positives by cause:"]
        lines += [
            f"    {cause:<14}{count:>3}   {_CAUSE_MEANS.get(cause, '')}"
            for cause, count in by_cause.items()
        ]
        fixable = report.fixable_false_positives
        total = sum(by_cause.values())
        lines += [
            "",
            f"    {fixable} of {total} are reachable by a change to this module. The rest are"
            " `no-noun`:",
            "    the meeting settled the item with a verb or a date and said no noun that could",
            "    name it, so neither a keyword list nor a better extractor gets to them. That is",
            "    a ceiling on matching keywords against topic labels, not a mistuning of it.",
        ]

    lines += ["", *_notes(report, extractor=extractor)]
    return "\n".join(lines)


_CAUSE_MEANS = {
    PARTIAL: "threshold -- AUTUNE_GAP_PARTIAL_CENTRALITY",
    EXTRACTION: "step 1 -- the noun was said and never became a topic (#278)",
    KEYWORD: "template -- the topic exists and the keywords do not name it",
    NO_NOUN: "out of reach -- no noun names the item in this meeting",
    "unclassified": "the case labels no evidence, so the split is not claimed",
}


def _headline(report: Report) -> str:
    if report.precision is None:
        return (
            "precision (high)     not measured -- no gap was surfaced by any case.\n"
            "                     Not 0.0 and not 1.0: both would be a claim about a\n"
            "                     pipeline that said nothing."
        )

    verdict = "PASS" if report.meets_six_week_target else "BELOW TARGET"
    out = [
        f"precision (high)     {report.precision:.4f}   <- the metric "
        f"(target {TARGET_PRECISION_SIX_WEEKS}, 3mo {TARGET_PRECISION_THREE_MONTHS}) -- {verdict}"
    ]
    if report.precision_any_severity is not None:
        out.append(f"precision (all)      {report.precision_any_severity:.4f}   every severity")
    out.append(
        f"recall (high)        {report.recall:.4f}   reported, not a target"
        if report.recall is not None
        else "recall (high)        not measured -- no case labels a real gap"
    )
    return "\n".join(out)


def _notes(report: Report, *, extractor: str) -> list[str]:
    notes = []

    if extractor == "fake":
        notes += [
            "note: AUTUNE_GAP_NER_IMPL=fake. `FakeNer` keys on a hand-written vocabulary",
            "      and is not an approximation of the model's accuracy (see pipeline.ner).",
            "      This number is about the fixtures, not about the pipeline. Run with",
            "      `--extra local-models` and AUTUNE_GAP_NER_IMPL=spacy for a real one.",
            "",
        ]

    empty = report.empty_graph_cases
    if empty:
        notes += [
            f"note: extraction found no topics in {len(empty)} case(s): "
            f"{[case.case_id for case in empty]}.",
            "      `detect.compare` raises nothing for an empty graph on purpose, so these",
            "      contribute no gaps to precision and pull recall down for a reason that",
            "      is about step 1 rather than steps 6 and 7.",
            "",
        ]

    notes += [
        "This set is authored meetings, not real ones. The PRD's precision figure comes",
        "from five to ten real team meetings in W5; four cases cannot carry a statistical",
        "claim. What it can do is fail when a template keyword starts matching everything",
        "or a threshold moves a band -- read it as a regression gate, not as the KPI.",
    ]
    return notes


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="autune_gap.eval", description=__doc__)
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"labeled set in eval/fixtures/ (default {DEFAULT_DATASET})",
    )
    args = parser.parse_args(argv)

    try:
        report = run_all(args.dataset)
    except EvalSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_report(report, extractor=get_settings().ner_impl))
    return 0 if report.meets_six_week_target else 1


if __name__ == "__main__":
    raise SystemExit(main())
