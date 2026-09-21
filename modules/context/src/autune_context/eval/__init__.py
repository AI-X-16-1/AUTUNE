"""Evaluation harness — this module's product KPIs, and the thresholds its own
config docstrings promised to tune against.

Two independent suites, each with its own hand-labeled dataset versioned next
to the code that scores it (see each suite's own ``dataset.py`` and
docs/engineering/testing.md, "Evaluation"):

- ``topic_linking`` — the PRD KPI (docs/product/prd.md section 12; 0.75+ at
  six weeks). Exercises the embedder + reranker.
- ``decision_lineage`` — not a named PRD KPI, but what
  ``ContextSettings.lineage_match_threshold`` and the NLI classification it
  gates are meant to be tuned against (see ``config.py``). Exercises the
  embedder + NLI model together, on decision-statement text rather than
  generic KorNLI pairs — a different question from
  ``autune_context.training``'s KorNLI benchmark number.

Run every suite: ``uv run --package autune-context python -m autune_context.eval``
Run one: ``uv run --package autune-context python -m autune_context.eval topic-linking``

A third suite is a plain subpackage away: give it its own ``dataset.py`` /
``runner.py`` / ``fixtures/*.json`` and add it to ``__main__``'s registry.
"""

from __future__ import annotations
