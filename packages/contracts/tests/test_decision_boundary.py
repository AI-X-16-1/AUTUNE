"""The one place module B and module D meet.

B decides *what counts as a decision in this meeting*. D decides *whether it is
the same decision as one from a past meeting*. These tests pin that split so
neither owner has to guess, and so a change to it fails loudly.

See docs/architecture/contracts.md and docs/modules/context.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autune_contracts import ContextLinks, ExtractionResult, fixtures


def test_b_publishes_decision_entities() -> None:
    """A decision is its own thing, not just an utterance labelled 'decision'.

    One decision may span several utterances; without an entity there is nothing
    for D to key a lineage on.
    """
    result = ExtractionResult.model_validate(fixtures.load("extraction_result"))
    assert result.decisions
    decision = result.decisions[0]
    assert decision.id.startswith("dec_")
    assert decision.statement
    assert len(decision.source_utterance_ids) >= 1


def test_d_keeps_two_identities_apart() -> None:
    """thread_id is D's lineage; source_decision_id is B's decision."""
    links = ContextLinks.model_validate(fixtures.load("context_links"))
    change = links.decision_lineage[0]
    assert change.thread_id.startswith("thr_")
    assert change.source_decision_id.startswith("dec_")
    assert change.thread_id != change.source_decision_id


def test_the_two_id_kinds_are_not_interchangeable() -> None:
    """Swapping them is the mistake this naming exists to prevent."""
    payload = fixtures.load("context_links")
    payload["decision_lineage"][0]["thread_id"] = "dec_014"
    with pytest.raises(ValidationError):
        ContextLinks.model_validate(payload)


def test_topic_links_survive_a_failure_in_b() -> None:
    """B failing must not cost the user their topic links.

    Topic linking needs only the transcript, so it runs in parallel with B.
    Decision lineage needs B, so it can be absent — and says so.
    """
    payload = fixtures.load("context_links") | {
        "decision_lineage": [],
        "missing_sources": ["extraction"],
    }
    links = ContextLinks.model_validate(payload)
    assert links.topic_links, "topic links must still be published"
    assert links.decision_lineage == []
    assert links.missing_sources == ["extraction"]


def test_a_complete_run_reports_nothing_missing() -> None:
    links = ContextLinks.model_validate(fixtures.load("context_links"))
    assert links.missing_sources == []
