"""Evaluation harness — module C's product KPI.

``modules/gap/CLAUDE.md``, ``docs/modules/gap.md`` and
``docs/engineering/testing.md`` have all three printed this command since the
repository was laid out, and nothing was behind it. Modules A, B and D each have
one; this closes C's.

    uv run --package autune-gap python -m autune_gap.eval

**Gap detection precision** — 0.70+ at six weeks, 0.82+ at three months
(docs/product/prd.md section 12). Precision, not recall: a false gap costs the
team's trust in every other gap on the screen, and a missed one costs nothing
they did not already not have.

Four modules in three files, split so that most of it is testable without a
database:

- ``dataset`` — loads and validates the labeled set. Refuses a case whose
  labels do not cover its template, because a precision figure over incomplete
  labels measures the labeler.
- ``metrics`` — pure scoring over sets of template item keys. No database, no
  model.
- ``runner`` — seeds a meeting, runs the real pipeline over it, reads the rows
  back. Needs a migrated Postgres and the ``local-models`` extra.

**The committed set is four authored meetings and is not the PRD number.** That
one comes from real team meetings in W5. This set exists so the harness works
before those exist, and so a regression in steps 6 and 7 — a template keyword
that starts matching everything, a threshold that moves a band — fails visibly
rather than silently. The report says as much at the bottom of every run.

**Exit code.** 0 when the six-week target is met, 1 when it is not *or* when
precision could not be measured at all, 2 when the evaluation set itself is
unusable. So it can be a gate, and an unmeasurable run does not read as a pass —
but it is deliberately not in the CI list (docs/engineering/testing.md, "CI
gates"): it needs a migrated Postgres and a 500 MB pipeline, and a number this
set cannot carry should not be able to block a pull request.
"""

from __future__ import annotations
