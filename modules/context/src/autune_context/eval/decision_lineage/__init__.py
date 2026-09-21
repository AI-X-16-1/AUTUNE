"""Decision-lineage accuracy: did a decision thread onto the right past
decision (or correctly onto none), and was the change classified correctly
(unchanged / reversed / modified)?

Not a named PRD KPI the way topic-linking accuracy is (docs/product/prd.md
section 12 has no row for it), but ``ContextSettings.lineage_match_threshold``
and the NLI classification it depends on are both called out in
``config.py`` as "tuned against the evaluation set once it exists" — this is
that set. It is deliberately domain text (decision statements), not generic
KorNLI sentence pairs: ``autune_context.training`` already measures the
fine-tuned NLI model against the public KorNLI benchmark, which is a
different question from whether *this* model, on *our* decision-statement
style, threads and classifies correctly end to end through
``service.build_decision_lineage`` (embedder + NLI together, not NLI alone).
"""

from __future__ import annotations
