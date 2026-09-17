"""``uv run --package autune-context python -m autune_context.eval [suite]``.

With no ``suite``, runs every registered suite. See ``eval``'s own docstring
for what each one measures and each suite's ``runner`` module for what it
needs to actually run (a migrated Postgres, at minimum).
"""

from __future__ import annotations

import argparse

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
    args = parser.parse_args()

    for name in [args.suite] if args.suite else sorted(_SUITES):
        module = _SUITES[name]
        print(f"=== {name} ===")
        print(module.report(module.run_all()))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
