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
import os
import sys
from collections.abc import Callable

from autune_gap.config import get_settings
from autune_gap.eval.dataset import DEFAULT_DATASET, EvalSetError
from autune_gap.eval.metrics import (
    EXTRACTION,
    KEYWORD,
    NO_NOUN,
    PARTIAL,
    TARGET_PRECISION_SIX_WEEKS,
    TARGET_PRECISION_THREE_MONTHS,
    CaseScore,
    Report,
)
from autune_gap.eval.runner import HarnessInconsistencyError, run_all
from autune_gap.pipeline.registry import get_sentence_embedder

EMBEDDERS = ("off", "local", "fake")


def format_report(report: Report, *, extractor: str, embedder: str = "off") -> str:
    lines = [
        f"Gap detection -- {len(report.cases)} cases, extractor {extractor}, embedder {embedder}",
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
            f"    {fixable} of {total} are reachable by a keyword or extraction change. The rest"
            " are `no-noun`:",
            "    the meeting settled the item with a verb or a date and said no noun that could",
            "    name it, so neither a keyword list nor a better extractor gets to them. The",
            "    sentence embedder (`--embedder local`) is what reads those; `--compare` shows",
            "    which of them it closes.",
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


def format_comparison(baseline: Report, candidate: Report, *, embedder: str) -> str:
    """The two runs side by side, and what moved between them item by item.

    Same cases, same extractor, same thresholds; the only difference is whether
    the speech was also read by meaning. Every false positive the baseline
    raised is listed with what became of it, so "no-noun went from 4 to 2" can
    be checked against which two, and every real gap the candidate stopped
    surfacing is listed as the price.
    """
    before = {case.case_id: case for case in baseline.cases}
    after = {case.case_id: case for case in candidate.cases}

    lines = [
        f"Comparison -- embedder off (baseline) vs {embedder}",
        "",
        f"{'':<22}{'off':>10}{embedder:>10}",
        _row("precision (high)", baseline.precision, candidate.precision),
        _row("recall (high)", baseline.recall, candidate.recall),
        _row("precision (all)", baseline.precision_any_severity, candidate.precision_any_severity),
        _count("true positives", baseline, candidate, lambda case: case.true_positives),
        _count("false positives", baseline, candidate, lambda case: case.false_positives),
    ]
    by_cause_before = baseline.false_positives_by_cause
    by_cause_after = candidate.false_positives_by_cause
    for cause in sorted(set(by_cause_before) | set(by_cause_after)):
        lines.append(
            f"{'  fp ' + cause:<22}{by_cause_before.get(cause, 0):>10}"
            f"{by_cause_after.get(cause, 0):>10}"
        )

    lines += ["", "  baseline false positives, and what became of them:"]
    for case_id, case in before.items():
        other = after[case_id]
        for item in sorted(case.false_positives):
            if item in other.false_positives:
                fate = f"still raised ({other.cause(item)})"
            elif item in other.raised_any:
                fate = "closed -- still raised, below high"
            else:
                fate = "closed -- no longer raised"
            lines.append(f"    {case_id:<24}{item:<18}{case.cause(item):<14}{fate}")

    new_fp = [
        f"{case_id}:{item}"
        for case_id, case in after.items()
        for item in sorted(case.false_positives - before[case_id].false_positives)
    ]
    lost = [
        f"{case_id}:{item}"
        for case_id, case in after.items()
        for item in sorted(case.missed - before[case_id].missed)
    ]
    lines += [
        "",
        f"  new false positives:          {new_fp or 'none'}",
        f"  real gaps no longer surfaced: {lost or 'none'}",
        "",
        "Four authored meetings, and the embedder's floor and margin were chosen by",
        "looking at them. A difference here says the mechanism does what it claims on",
        "these cases; whether it holds is for the W5 meetings to say.",
    ]
    return "\n".join(lines)


def _row(name: str, left: float | None, right: float | None) -> str:
    def cell(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    return f"{name:<22}{cell(left):>10}{cell(right):>10}"


def _count(
    name: str, left: Report, right: Report, items: Callable[[CaseScore], frozenset[str]]
) -> str:
    def total(report: Report) -> int:
        return sum(len(items(case)) for case in report.cases)

    return f"{name:<22}{total(left):>10}{total(right):>10}"


def _with_embedder(impl: str, run: Callable[[], Report]) -> Report:
    """Run with ``AUTUNE_GAP_EMBEDDER_IMPL`` set to ``impl``, then put it back.

    Settings and the embedder are both cached per process, so both caches are
    dropped on the way in and on the way out. The entity extractor's is not:
    reloading spaCy between two runs would change nothing but the time.
    """
    previous = os.environ.get("AUTUNE_GAP_EMBEDDER_IMPL")
    os.environ["AUTUNE_GAP_EMBEDDER_IMPL"] = impl
    get_settings.cache_clear()
    get_sentence_embedder.cache_clear()
    try:
        return run()
    finally:
        if previous is None:
            os.environ.pop("AUTUNE_GAP_EMBEDDER_IMPL", None)
        else:
            os.environ["AUTUNE_GAP_EMBEDDER_IMPL"] = previous
        get_settings.cache_clear()
        get_sentence_embedder.cache_clear()


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
    parser.add_argument(
        "--embedder",
        choices=EMBEDDERS,
        default=None,
        help="sentence embedder for this run (default: AUTUNE_GAP_EMBEDDER_IMPL)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="run twice -- embedder off, then --embedder (default local) -- and compare",
    )
    args = parser.parse_args(argv)

    embedder = args.embedder or ("local" if args.compare else get_settings().embedder_impl)
    if args.compare and embedder == "off":
        parser.error("--compare needs an embedder to compare against: local or fake")

    extractor = get_settings().ner_impl
    try:
        baseline = _with_embedder("off", lambda: run_all(args.dataset)) if args.compare else None
        report = _with_embedder(embedder, lambda: run_all(args.dataset))
    except (EvalSetError, HarnessInconsistencyError) as exc:
        # Both mean the report would be a number about something other than
        # what it claims. Exit 2 rather than 1: nothing was measured, so this is
        # not "below target".
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if baseline is not None:
        print(format_report(baseline, extractor=extractor, embedder="off"))
        print()
    print(format_report(report, extractor=extractor, embedder=embedder))
    if baseline is not None:
        print()
        print(format_comparison(baseline, report, embedder=embedder))
    return 0 if report.meets_six_week_target else 1


if __name__ == "__main__":
    raise SystemExit(main())
