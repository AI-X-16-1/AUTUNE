"""Grouping decision labels into the entity module D keys a lineage on.

No database. The grouping is arithmetic over labels and the privacy properties
are facts about the table definitions, so both are readable without Postgres.

The boundary these pin is stated once in
``packages/contracts/tests/test_decision_boundary.py``; these are the module-B
half of it.
"""

from __future__ import annotations

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.decisions import (
    DEFAULT_MAX_GAP,
    ClassifiedUtterance,
    DecisionGroup,
    group_decisions,
)
from autune_extraction.models import ExtDecision, ExtDecisionSource

CHAT = UtteranceKind.CONCERN
"""Any label that is not a decision. Named so the tests below read as "talk in
between" rather than as a claim about concerns."""


def utterance(
    id_: str, kind: UtteranceKind, *, confidence: float = 0.9, text: str = "..."
) -> ClassifiedUtterance:
    return ClassifiedUtterance(id=id_, kind=kind, confidence=confidence, text=text)


# --- what a decision is -----------------------------------------------------


def test_consecutive_decision_utterances_are_one_decision_not_several() -> None:
    """The whole reason the entity exists.

    Three labelled utterances are one thing the meeting settled. Emitting three
    decisions would hand D three lineages for one decision, and the summary tab
    would then disagree with the lineage view.
    """
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION),
            utterance("utt_2", UtteranceKind.DECISION),
            utterance("utt_3", UtteranceKind.DECISION),
        ]
    )

    assert len(groups) == 1
    assert groups[0].source_utterance_ids == ("utt_1", "utt_2", "utt_3")


def test_a_stretch_of_other_talk_separates_two_decisions() -> None:
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION),
            utterance("utt_2", CHAT),
            utterance("utt_3", CHAT),
            utterance("utt_4", CHAT),
            utterance("utt_5", UtteranceKind.DECISION),
        ],
        max_gap=2,
    )

    assert [group.source_utterance_ids for group in groups] == [("utt_1",), ("utt_5",)]


def test_a_short_aside_does_not_split_a_decision() -> None:
    """A meeting rarely settles something in consecutive sentences.

    Somebody agrees, somebody asks a question, and the decision lands after. That
    is one decision, and requiring adjacency would fragment nearly all of them.
    """
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION),
            utterance("utt_2", CHAT),
            utterance("utt_3", UtteranceKind.DECISION),
        ],
        max_gap=2,
    )

    assert len(groups) == 1
    assert groups[0].source_utterance_ids == ("utt_1", "utt_3")


def test_the_gap_is_counted_in_utterances_not_in_labelled_ones() -> None:
    """The in-between utterances have to be passed in, so filtering is a bug.

    Handing this only the decision-labelled utterances makes every decision in
    the meeting adjacent, and a whole meeting comes back as one decision.
    """
    full_meeting = [
        utterance("utt_1", UtteranceKind.DECISION),
        *[utterance(f"utt_{n}", CHAT) for n in range(2, 12)],
        utterance("utt_12", UtteranceKind.DECISION),
    ]
    only_labelled = [u for u in full_meeting if u.kind is UtteranceKind.DECISION]

    assert len(group_decisions(full_meeting)) == 2
    assert len(group_decisions(only_labelled)) == 1


def test_a_meeting_that_settled_nothing_produces_no_decisions() -> None:
    assert group_decisions([utterance("utt_1", CHAT), utterance("utt_2", CHAT)]) == []


def test_an_empty_meeting_produces_no_decisions() -> None:
    assert group_decisions([]) == []


def test_a_run_still_open_at_the_end_of_the_meeting_is_kept() -> None:
    """The run is closed by the end of the transcript, not only by a gap."""
    groups = group_decisions(
        [
            utterance("utt_1", CHAT),
            utterance("utt_2", UtteranceKind.DECISION),
        ],
        max_gap=2,
    )

    assert [group.source_utterance_ids for group in groups] == [("utt_2",)]


# --- what the entity carries ------------------------------------------------


def test_the_statement_is_where_the_decision_settled() -> None:
    """The last member, not the first: the contract asks for it as settled.

    Quoting the first would publish the proposal the meeting moved past.
    """
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text="인기순이 나을까요"),
            utterance("utt_2", UtteranceKind.DECISION, text="검색 정렬은 인기순으로 진행"),
        ]
    )

    assert groups[0].statement == "검색 정렬은 인기순으로 진행"


def test_a_decision_is_not_penalised_for_taking_several_turns() -> None:
    """Confidence is the highest member's, not the mean.

    Averaging would score a decision lower the longer it was discussed, pushing
    the multi-utterance case this entity exists for below any threshold set on
    it.
    """
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, confidence=0.55),
            utterance("utt_2", UtteranceKind.DECISION, confidence=0.91),
            utterance("utt_3", UtteranceKind.DECISION, confidence=0.60),
        ]
    )

    assert groups[0].confidence == 0.91


def test_sources_keep_meeting_order() -> None:
    """The order is the argument: the proposal first, the settlement last."""
    groups = group_decisions(
        [
            utterance("utt_9", UtteranceKind.DECISION),
            utterance("utt_1", UtteranceKind.DECISION),
        ]
    )

    assert groups[0].source_utterance_ids == ("utt_9", "utt_1")


def test_a_negative_gap_is_refused_rather_than_clamped() -> None:
    with pytest.raises(ValueError, match="max_gap"):
        group_decisions([], max_gap=-1)


def test_a_group_is_comparable_by_value() -> None:
    """Frozen and equatable, so a test can state a whole expected decision."""
    groups = group_decisions([utterance("utt_1", UtteranceKind.DECISION, text="가자")])

    assert groups == [
        DecisionGroup(statement="가자", source_utterance_ids=("utt_1",), confidence=0.9)
    ]


# --- the boundary with module D ---------------------------------------------


def mint_id() -> str:
    """The id ``ext_decisions`` would generate on insert.

    Read off the column default rather than by inserting a row: the default is
    where the prefix is decided, and reaching it needs no database.
    """
    return str(ExtDecision.__table__.c.id.default.arg(None))


def test_a_decision_id_is_not_a_thread_id() -> None:
    """``dec_`` is what this meeting settled; ``thr_`` is D's lineage.

    ``packages/contracts/tests/test_decision_boundary.py`` refuses a ``dec_``
    where a ``thr_`` belongs. This is the other end of it: the id this module
    mints is the one that test expects to see.
    """
    decision_id = mint_id()

    assert decision_id.startswith("dec_")
    assert not decision_id.startswith("thr_")


def test_each_decision_gets_its_own_id() -> None:
    assert mint_id() != mint_id()


# --- what the tables must not hold ------------------------------------------


def test_a_decision_belongs_to_the_meeting_and_carries_no_owner() -> None:
    """ADR 0007: reachable by ``meeting_id``, so it survives a departure.

    A decision is the clearest case — the team is still bound by it after the
    person who proposed it leaves. Asserting the whole column set rather than the
    absence of one name means an owner-shaped column added later fails here,
    where the reason is written down.
    """
    columns = {column.name for column in ExtDecision.__table__.columns}

    assert columns == {
        "id",
        "meeting_id",
        "statement",
        "confidence",
        "created_at",
        "updated_at",
    }


def test_decision_sources_hold_a_link_and_a_position_only() -> None:
    """No copy of the utterance text.

    The quotation is read by joining ``utterances``, so a deleted meeting takes
    it along. Denormalising the text here would leave transcript content behind a
    cascade that no longer reaches it.
    """
    columns = {column.name for column in ExtDecisionSource.__table__.columns}

    assert columns == {"id", "decision_id", "utterance_id", "position"}


def test_both_tables_are_deleted_with_their_meeting() -> None:
    """Every ext_ table needs a path to deletion by meeting_id (data-model.md)."""
    meeting_fk = next(
        fk for fk in ExtDecision.__table__.foreign_keys if fk.column.table.name == "meetings"
    )
    decision_fk = next(iter(ExtDecisionSource.__table__.c.decision_id.foreign_keys))

    assert meeting_fk.ondelete == "CASCADE"
    assert decision_fk.ondelete == "CASCADE"


# --- the tunable ------------------------------------------------------------


def test_the_default_gap_is_a_named_constant() -> None:
    """It is a guess the evaluation set is meant to settle (ADR 0006).

    Pinning it means changing it is a deliberate edit with a test to update, not
    a number somebody nudges while reading the grouping code.
    """
    assert DEFAULT_MAX_GAP == 2
