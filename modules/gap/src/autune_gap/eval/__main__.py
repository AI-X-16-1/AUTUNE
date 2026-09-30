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
from autune_gap.eval.probes import ProbeResult, format_probes, load_probes, run_probe
from autune_gap.eval.runner import HarnessInconsistencyError, run_all
from autune_gap.pipeline.registry import (
    get_relation_extractor,
    get_sentence_embedder,
    get_template_verifier,
)

EMBEDDERS = ("off", "local", "fake")
VERIFIERS = ("off", "fake", "gemini")
RELATIONS = ("rule", "gemini")


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
    """Two runs side by side; see ``format_runs``."""
    return format_runs([("off", baseline), (embedder, candidate)])


def format_runs(runs: list[tuple[str, Report]]) -> str:
    """The runs side by side, and what moved from the first to each of the others.

    Same cases, same extractor, same thresholds; the runs differ only in how the
    speech was read. Every false positive the first run raised is listed with
    what became of it in each later one, so "no-noun went from 4 to 2" can be
    checked against which two, and every real gap a later run stopped surfacing
    is listed as its price.
    """
    labels = [label for label, _ in runs]
    reports = [report for _, report in runs]
    baseline = reports[0]

    lines = [
        "Comparison -- " + " vs ".join(labels),
        "",
        f"{'':<22}" + "".join(f"{label:>14}" for label in labels),
        _row("precision (high)", [report.precision for report in reports]),
        _row("recall (high)", [report.recall for report in reports]),
        _row("precision (all)", [report.precision_any_severity for report in reports]),
        _count("true positives", reports, lambda case: case.true_positives),
        _count("false positives", reports, lambda case: case.false_positives),
    ]
    causes = sorted({cause for report in reports for cause in report.false_positives_by_cause})
    for cause in causes:
        lines.append(
            f"{'  fp ' + cause:<22}"
            + "".join(f"{report.false_positives_by_cause.get(cause, 0):>14}" for report in reports)
        )

    before = {case.case_id: case for case in baseline.cases}
    for label, report in runs[1:]:
        after = {case.case_id: case for case in report.cases}
        lines += ["", f"  {labels[0]} false positives, and what became of them in {label}:"]
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
            f"  new false positives:          {new_fp or 'none'}",
            f"  real gaps no longer surfaced: {lost or 'none'}",
        ]

    lines += [
        "",
        "Four authored meetings, and the embedder's and the triage's thresholds were",
        "chosen by looking at them. A difference here says the mechanism does what it",
        "claims on these cases; whether it holds is for the W5 meetings to say.",
    ]
    return "\n".join(lines)


def _row(name: str, values: list[float | None]) -> str:
    def cell(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    return f"{name:<22}" + "".join(f"{cell(value):>14}" for value in values)


def _count(name: str, reports: list[Report], items: Callable[[CaseScore], frozenset[str]]) -> str:
    def total(report: Report) -> int:
        return sum(len(items(case)) for case in report.cases)

    return f"{name:<22}" + "".join(f"{total(report):>14}" for report in reports)


_SETTINGS = {
    "embedder": "AUTUNE_GAP_EMBEDDER_IMPL",
    "verifier": "AUTUNE_GAP_VERIFIER_IMPL",
    "relations": "AUTUNE_GAP_RELATION_IMPL",
}


def _configured[T](run: Callable[[], T], **impls: str) -> T:
    """Run with the given implementations set, then put the environment back.

    Settings and the models are cached per process, so the caches are dropped
    on the way in and on the way out. The entity extractor's is not: reloading
    spaCy between runs would change nothing but the time.
    """
    previous = {name: os.environ.get(_SETTINGS[name]) for name in impls}
    for name, impl in impls.items():
        os.environ[_SETTINGS[name]] = impl
    _reset()
    try:
        return run()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(_SETTINGS[name], None)
            else:
                os.environ[_SETTINGS[name]] = value
        _reset()


def _reset() -> None:
    get_settings.cache_clear()
    get_sentence_embedder.cache_clear()
    get_template_verifier.cache_clear()
    get_relation_extractor.cache_clear()


def _verifier_load() -> str:
    """How much the run sent to the verifier, read off the cached instance —
    utterances asked about, and requests made when the implementation counts
    them."""
    verifier = get_template_verifier()
    if verifier is None:
        return ""
    asked = len(getattr(verifier, "asked", ()))
    requests = getattr(verifier, "requests", None)
    load = f"verifier {verifier.model_version}: {asked} utterance(s) asked"
    return load + (f" in {requests} request(s)" if requests is not None else "")


def _relation_load() -> str:
    """How much the run sent for relation assistance, or "" under the rules
    alone. Read off the cached asker, as ``_verifier_load`` reads the verifier."""
    asker = getattr(get_relation_extractor(), "asker", None)
    if asker is None:
        return ""
    asked = len(getattr(asker, "asked", ()))
    requests = getattr(asker, "requests", None)
    load = f"relation assist {asker.model_version}: {asked} utterance(s) asked"
    return load + (f" in {requests} request(s)" if requests is not None else "")


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
        "--verifier",
        choices=VERIFIERS,
        default=None,
        help="template verifier for this run (default: AUTUNE_GAP_VERIFIER_IMPL). "
        "gemini sends the ambiguous utterances of the eval set to Google",
    )
    parser.add_argument(
        "--relations",
        choices=RELATIONS,
        default=None,
        help="relation extractor for every run (default: AUTUNE_GAP_RELATION_IMPL). "
        "gemini sends the utterances the rules decline to Google",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="run embedder off, then --embedder (default local), then -- when --verifier "
        "is not off -- the same with the verifier, and compare",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="run the single-utterance verification probes instead of the meetings",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    embedder = args.embedder or ("local" if args.compare or args.probe else settings.embedder_impl)
    verifier = args.verifier or settings.verifier_impl
    relations = args.relations or settings.relation_impl
    if (args.compare or args.probe) and embedder == "off":
        parser.error("--compare and --probe need an embedder: local or fake")

    if args.probe:
        return _probe(embedder=embedder, verifier=verifier)

    extractor = settings.ner_impl
    loads: list[str] = []

    def measured(impls: dict[str, str]) -> Callable[[], Report]:
        def run() -> Report:
            report = run_all(args.dataset)
            loads.append("\n".join(load for load in (_verifier_load(), _relation_load()) if load))
            return report

        return lambda: _configured(run, relations=relations, **impls)

    try:
        if args.compare:
            runs = [
                ("off", measured({"embedder": "off", "verifier": "off"})()),
                (embedder, measured({"embedder": embedder, "verifier": "off"})()),
            ]
            if verifier != "off":
                runs.append(
                    (f"+{verifier}", measured({"embedder": embedder, "verifier": verifier})())
                )
        else:
            runs = [(embedder, measured({"embedder": embedder, "verifier": verifier})())]
    except (EvalSetError, HarnessInconsistencyError) as exc:
        # Both mean the report would be a number about something other than
        # what it claims. Exit 2 rather than 1: nothing was measured, so this is
        # not "below target".
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for (label, report), load in zip(runs, loads, strict=True):
        print(format_report(report, extractor=extractor, embedder=label))
        if load:
            print(load)
        print()
    if len(runs) > 1:
        print(format_runs(runs))
    return 0 if runs[-1][1].meets_six_week_target else 1


def _probe(*, embedder: str, verifier: str) -> int:
    probes = load_probes()
    with_verifier = verifier != "off"

    def run() -> list[ProbeResult]:
        return [run_probe(probe, with_verifier=with_verifier) for probe in probes]

    results = _configured(run, embedder=embedder, verifier=verifier)
    print(format_probes(results, verifier=verifier))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
