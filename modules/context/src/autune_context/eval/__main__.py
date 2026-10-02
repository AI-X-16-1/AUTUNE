"""``uv run --package autune-context python -m autune_context.eval [suite]``.

With no ``suite``, runs every registered suite. See ``eval``'s own docstring
for what each one measures and each suite's ``runner`` module for what it
needs to actually run (a migrated Postgres, at minimum). Refuses a database
that holds meetings it did not create -- see ``_guard``.

``--mode classic|llm|hybrid`` runs under that ``engine_mode`` instead of whatever
the environment says; ``both`` (classic, llm) and ``all`` (classic, llm, hybrid) run
each suite once per mode and print them side by side, classic as the baseline.
Every mode but ``classic`` calls the external LLM, which costs money and needs
``AUTUNE_CONTEXT_LLM_API_KEY``; the cost of the run is printed at the end of it.
"""

from __future__ import annotations

import argparse

from autune_context.eval._guard import refuse_real_meetings
from autune_context.eval._mode import comparison, engine_mode, llm_usage_line
from autune_context.eval.decision_lineage import runner as decision_lineage
from autune_context.eval.topic_linking import runner as topic_linking

_SUITES = {
    "topic-linking": topic_linking,
    "decision-lineage": decision_lineage,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="autune_context.eval")
    parser.add_argument(
        "suite",
        nargs="?",
        choices=sorted(_SUITES),
        help="run only this suite; default runs all of them",
    )
    parser.add_argument(
        "--dataset",
        help="fixture file name under the suite's fixtures/ (e.g. the held-out "
        "set); needs a suite. Default: the suite's own default dataset",
    )
    parser.add_argument(
        "--mode",
        choices=("classic", "llm", "hybrid", "both", "all"),
        help="engine_mode to run under (default: the environment's). 'both' runs "
        "classic then llm, 'all' adds hybrid; each prints a comparison",
    )
    args = parser.parse_args()
    if args.dataset and not args.suite:
        parser.error("--dataset needs a suite")
    refuse_real_meetings()

    for name in [args.suite] if args.suite else sorted(_SUITES):
        module = _SUITES[name]
        cases = module.load_cases(args.dataset) if args.dataset else None
        label = f"{name}{f' ({args.dataset})' if args.dataset else ''}"

        if args.mode is None:
            print(f"=== {label} ===")
            print(module.report(module.run_all(cases)))
            print()
        else:
            _run_modes(name, label, module, cases, _MODES_OF[args.mode])
    return 0


_MODES_OF = {
    "classic": ("classic",),
    "llm": ("llm",),
    "hybrid": ("hybrid",),
    "both": ("classic", "llm"),
    "all": ("classic", "llm", "hybrid"),
}


def _run_modes(name: str, label: str, module, cases, modes: tuple[str, ...]) -> None:
    runs = {}
    for mode in modes:
        print(f"=== {label}, engine_mode={mode} ===")
        with engine_mode(mode):
            results = module.run_all(cases)
            print(module.report(results))
            if mode != "classic":
                print(llm_usage_line())
        runs[mode] = (module.headline(results), module.case_outcomes(results))
        print()
    if len(runs) > 1:
        print(comparison(name, runs))
        print()


if __name__ == "__main__":
    raise SystemExit(main())
