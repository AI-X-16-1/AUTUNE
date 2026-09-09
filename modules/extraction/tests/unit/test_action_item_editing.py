"""Editing an action item: the metric it feeds, and what it must not record.

Nothing here needs a database. The arithmetic lives in ``edit_cost``, the request
shapes in ``schemas``, and the privacy property is a fact about the table
definition — all three are readable without Postgres, which is the point of
keeping them apart from ``service``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autune_extraction.edit_cost import EditCost, summarise
from autune_extraction.models import ExtActionItem, ExtEditEvent
from autune_extraction.schemas import ActionItemCreate, ActionItemUpdate

MEETING = "mtg_1"


# --- what edit cost counts --------------------------------------------------


def test_a_list_nobody_touched_is_a_clean_acceptance() -> None:
    """The number the product is trying to reach."""
    cost = EditCost(model_items=5, edited_items=0, added_items=0, edits=0)

    assert cost.clean_acceptance_rate == 1.0
    assert cost.is_untouched


def test_the_rate_is_the_share_of_model_items_left_alone() -> None:
    cost = EditCost(model_items=4, edited_items=1, added_items=0, edits=1)

    assert cost.clean_acceptance_rate == 0.75
    assert not cost.is_untouched


def test_a_meeting_the_model_found_nothing_in_scores_one_not_zero() -> None:
    """A stand-up with no commitments is not the model missing anything.

    Zero over zero has to be decided rather than derived, and scoring it 0.0
    would drag the average down for meetings that were never a test of recall.
    ``model_items`` travels alongside so an average can be weighted.
    """
    cost = EditCost(model_items=0, edited_items=0, added_items=0, edits=0)

    assert cost.clean_acceptance_rate == 1.0


def test_an_added_item_counts_as_an_edit() -> None:
    """An item the model missed costs more than one it got wrong.

    The user has to notice the absence. That is the failure recall makes likely
    and the one editing cannot fix by itself, so it cannot be free in the metric.
    """
    cost = EditCost(model_items=2, edited_items=0, added_items=1, edits=1)

    assert cost.edits_to_acceptance == 1
    assert not cost.is_untouched
    assert cost.clean_acceptance_rate == 1.0, "the model's own items were all kept"


def test_editing_one_item_twice_counts_twice_but_the_item_once() -> None:
    """Cost is what the user spent; acceptance is how many items were wrong."""
    cost = EditCost(model_items=3, edited_items=1, added_items=0, edits=2)

    assert cost.edits_to_acceptance == 2
    assert cost.clean_acceptance_rate == pytest.approx(2 / 3)


def test_summarise_counts_a_kind_it_does_not_know() -> None:
    """A new kind is a schema change that outran the metric.

    Dropping it silently would understate the cost, which is the direction that
    flatters us.
    """
    assert summarise(["created", "edited", "edited", "reopened"]) == {
        "created": 1,
        "edited": 2,
        "reopened": 1,
    }


# --- what the tables must not hold ------------------------------------------


def test_the_edit_event_table_has_no_person_on_it() -> None:
    """ADR 0003 forbids per-person metrics, and this is where one would appear.

    "Who corrects the model most" is the same shape of data as a speaking ratio:
    it describes one person's conduct in a meeting. The metric only asks how
    many, so the row only records that a correction happened. A column added here
    later would be the moment that changed, which is why this is a test and not a
    comment.
    """
    columns = set(ExtEditEvent.__table__.columns.keys())

    assert columns == {"id", "meeting_id", "action_item_id", "kind", "created_at"}
    assert not any("user" in name or "speaker" in name for name in columns)


def test_an_edit_event_outlives_the_item_it_refers_to() -> None:
    """A deletion event whose row is gone still has to count.

    SET NULL rather than CASCADE: cascading would delete the evidence that the
    model was wrong along with the wrong item, and edit cost would improve every
    time someone removed something.
    """
    fk = next(iter(ExtEditEvent.__table__.c.action_item_id.foreign_keys))

    assert fk.ondelete == "SET NULL"
    assert ExtEditEvent.__table__.c.action_item_id.nullable


def test_action_items_are_deleted_with_their_meeting() -> None:
    """privacy.md section 4: every module table reaches deletion by meeting_id."""
    fk = next(iter(ExtActionItem.__table__.c.meeting_id.foreign_keys))

    assert fk.ondelete == "CASCADE"


def test_a_departed_assignee_leaves_the_item_standing() -> None:
    """The commitment does not stop having been made. See ADR 0007."""
    fk = next(iter(ExtActionItem.__table__.c.assignee_id.foreign_keys))

    assert fk.ondelete == "SET NULL"
    assert ExtActionItem.__table__.c.assignee_id.nullable


# --- what a request may say -------------------------------------------------


def test_a_hand_added_item_cannot_carry_a_confidence() -> None:
    """A person typing an item is the certainty; the service stores 1.0.

    Letting a caller set it would put a model score on a human judgement and
    quietly corrupt the comparison edit cost exists to make.
    """
    with pytest.raises(ValidationError):
        ActionItemCreate(meeting_id=MEETING, description="계약서 검토", confidence=0.4)


def test_an_update_cannot_move_an_item_to_another_meeting() -> None:
    with pytest.raises(ValidationError):
        ActionItemUpdate(meeting_id="mtg_other")


def test_an_update_cannot_rewrite_where_the_item_came_from() -> None:
    """origin is what edit cost is measured on."""
    with pytest.raises(ValidationError):
        ActionItemUpdate(origin="model")


def test_clearing_a_due_date_is_a_change_not_an_absence() -> None:
    """``exclude_unset``, not ``exclude_none``.

    Without it, "remove the deadline" and "say nothing about the deadline" are
    the same request, and one of them is a correction the metric should count.
    """
    assert ActionItemUpdate(due_date=None).changes() == {"due_date": None}
    assert ActionItemUpdate().changes() == {}


def test_an_empty_update_asks_for_nothing() -> None:
    """The service records no edit for it — a double-submitted form is not a cost."""
    assert ActionItemUpdate().changes() == {}


def test_duplicate_source_utterances_are_accepted_and_deduplicated_later() -> None:
    """The schema takes what it is given; the service is where the set is made."""
    payload = ActionItemCreate(
        meeting_id=MEETING,
        description="계약서 검토",
        source_utterance_ids=["utt_1", "utt_1", "utt_2"],
    )

    assert payload.source_utterance_ids == ["utt_1", "utt_1", "utt_2"]
    assert list(dict.fromkeys(payload.source_utterance_ids)) == ["utt_1", "utt_2"]


def test_an_item_with_no_source_utterance_is_valid() -> None:
    """That is what "the model missed it" means, and the drawer renders it."""
    payload = ActionItemCreate(meeting_id=MEETING, description="계약서 검토")

    assert payload.source_utterance_ids == []
