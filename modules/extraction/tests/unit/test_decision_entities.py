"""Grouping decision labels into the entity module D keys a lineage on.

No database. The grouping is arithmetic over labels and the privacy properties
are facts about the table definitions, so both are readable without Postgres.

The boundary these pin is stated once in
``packages/contracts/tests/test_decision_boundary.py``; these are the module-B
half of it.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from sqlalchemy import Integer

from autune_contracts.enums import UtteranceKind
from autune_extraction.decisions import (
    DEFAULT_MAX_GAP,
    ClassifiedUtterance,
    DecisionGroup,
    decision_id,
    decision_of,
    group_decisions,
    identified,
)
from autune_extraction.models import ExtDecision, ExtDecisionSource

CHAT = UtteranceKind.CONCERN
"""Any label that is not a decision. Named so the tests below read as "talk in
between" rather than as a claim about concerns."""


def utterance(
    id_: str, kind: UtteranceKind, *, confidence: float = 0.9, text: str = "...", speaker: str = ""
) -> ClassifiedUtterance:
    return ClassifiedUtterance(id=id_, kind=kind, confidence=confidence, text=text, speaker=speaker)


# --- what a decision is -----------------------------------------------------


def test_consecutive_decision_utterances_are_one_decision_not_several() -> None:
    """The whole reason the entity exists.

    Three labelled utterances are one thing the meeting settled. Emitting three
    decisions would hand D three lineages for one decision, and the summary tab
    would then disagree with the lineage view.

    True of turns that only agree or point, as these three-character ones do.
    Turns that each say what was decided are told apart further down.
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


# --- turns that each say something are decisions of their own (2026-10-09) -----

LOGIN = "로그인은 소셜 로그인만 지원하기로 했습니다"
SEARCH = "검색 기능은 다음 분기로 미루기로 했습니다"
PRICE = "가격은 월 9900원으로 갑니다"
WEEKDAY = "배포 요일은 화요일로 바꾸기로 했습니다"
OCTOBER_8 = date(2026, 10, 8)


def test_a_wrap_up_that_lists_three_decisions_is_three_decisions() -> None:
    """The invented meeting the rule came from: read as one run it was one row
    quoting the price, with the login and the search in no row at all."""
    groups = group_decisions(
        [
            utterance("utt_1", CHAT, text="그럼 오늘 나온 얘기 정리해 보죠"),
            utterance("utt_2", UtteranceKind.DECISION, text=LOGIN),
            utterance("utt_3", UtteranceKind.DECISION, text=SEARCH),
            utterance("utt_4", UtteranceKind.DECISION, text=PRICE),
            utterance("utt_5", CHAT, text="네 좋아요"),
        ],
        day=OCTOBER_8,
    )

    assert [(group.statement, group.source_utterance_ids) for group in groups] == [
        ("로그인은 소셜 로그인만 지원하기로 함", ("utt_2",)),
        ("검색 기능은 다음 분기로 미루기로 함", ("utt_3",)),
        ("가격은 월 9900원으로 진행", ("utt_4",)),
    ]


def test_assent_belongs_with_the_decision_it_follows_not_with_the_next() -> None:
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text=LOGIN),
            utterance("utt_2", UtteranceKind.DECISION, text="네 그렇게 하죠"),
            utterance("utt_3", UtteranceKind.DECISION, text=PRICE),
            utterance("utt_4", UtteranceKind.DECISION, text="그럼 그대로 가죠"),
        ]
    )

    assert [group.source_utterance_ids for group in groups] == [
        ("utt_1", "utt_2"),
        ("utt_3", "utt_4"),
    ]
    assert [group.core_text for group in groups] == [LOGIN, PRICE]


def test_a_date_said_for_one_decision_is_not_the_deadline_of_the_next() -> None:
    """Read as one run, the payment decision carried the release date."""
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text="릴리스는 10월 20일에 내기로 했습니다"),
            utterance("utt_2", UtteranceKind.DECISION, text="결제 모듈은 외부 라이브러리로 갑니다"),
        ],
        day=OCTOBER_8,
    )

    assert [group.statement for group in groups] == [
        "릴리스는 10월 20일에 내기로 함 (기한 2026-10-20)",
        "결제 모듈은 외부 라이브러리로 진행",
    ]


def test_one_decision_said_twice_in_full_is_two_rows() -> None:
    """The cost of the rule, taken knowingly: a person deletes one."""
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text="검색 정렬은 인기순으로 바꾸시죠"),
            utterance(
                "utt_2", UtteranceKind.DECISION, text="네 검색 정렬은 인기순으로 바꾸기로 했습니다"
            ),
        ]
    )

    assert len(groups) == 2


@pytest.mark.parametrize(
    "text",
    [
        WEEKDAY,
        "릴리스는 10월 20일로 정하기로 했습니다",
        "정기 회의는 매주 월요일에 하기로 했습니다",
        "회고는 금요일마다 하기로 했습니다",
    ],
)
def test_a_date_that_is_what_was_decided_is_not_a_deadline(text: str) -> None:
    """ "화요일로 바꾸기로 함 (기한 2026-10-13)" set a deadline nobody had."""
    (group,) = group_decisions(
        [utterance("utt_1", UtteranceKind.DECISION, text=text)], day=OCTOBER_8
    )

    assert "기한" not in group.statement
    assert group.suffix == ""


@pytest.mark.parametrize(
    ("text", "due"),
    [
        ("릴리스는 10월 20일에 내기로 했습니다", "2026-10-20"),
        ("견적서는 다음 주 금요일까지 받기로 했습니다", "2026-10-16"),
        ("마감은 금요일까지로 하기로 했습니다", "2026-10-09"),
    ],
)
def test_a_date_something_is_due_by_is_still_the_deadline(text: str, due: str) -> None:
    (group,) = group_decisions(
        [utterance("utt_1", UtteranceKind.DECISION, text=text)], day=OCTOBER_8
    )

    assert group.suffix == f"기한 {due}"


# --- turns a kept row holds (``service.build_decisions``) ---------------------


def test_no_decision_is_made_again_from_turns_a_kept_row_holds() -> None:
    read = [
        utterance("utt_1", UtteranceKind.DECISION, text=LOGIN),
        utterance("utt_2", UtteranceKind.DECISION, text=SEARCH),
        utterance("utt_3", UtteranceKind.DECISION, text=PRICE),
    ]

    groups = group_decisions(read, held={"utt_1", "utt_2"})

    assert [group.source_utterance_ids for group in groups] == [("utt_3",)]


def test_assent_to_a_held_decision_is_not_a_decision_by_itself() -> None:
    """Its content is in the kept row. Left alone it would be a row reading
    "네 그렇게 하죠"."""
    read = [
        utterance("utt_1", UtteranceKind.DECISION, text=PRICE),
        utterance("utt_2", UtteranceKind.DECISION, text="네 그렇게 하죠"),
    ]

    assert group_decisions(read, held={"utt_1"}) == []
    assert len(group_decisions(read)) == 1


def test_a_decision_whose_assent_alone_is_held_is_made_from_the_rest() -> None:
    read = [
        utterance("utt_1", UtteranceKind.DECISION, text="그럼 그렇게 하죠"),
        utterance("utt_2", CHAT, text="금요일까지 하면 될까요"),
        utterance("utt_3", UtteranceKind.DECISION, text=PRICE),
    ]

    (group,) = group_decisions(read, held={"utt_1"}, day=OCTOBER_8)

    assert group.source_utterance_ids == ("utt_3",)
    assert group.statement == "가격은 월 9900원으로 진행", "and reads only what it spans"


def test_a_decision_can_be_read_from_exactly_the_turns_named() -> None:
    """What a kept row whose line was corrected is read again from."""
    read = [
        utterance("utt_1", UtteranceKind.DECISION, text=LOGIN),
        utterance("utt_2", UtteranceKind.DECISION, text=PRICE),
        utterance("utt_3", UtteranceKind.DECISION, text=SEARCH),
    ]

    group = decision_of(read, {"utt_1", "utt_2"})

    assert group is not None
    assert group.source_utterance_ids == ("utt_1", "utt_2")
    assert group.statement == "가격은 월 9900원으로 진행"
    assert (group.first_position, group.last_position) == (0, 1)
    assert decision_of(read, {"utt_9"}) is None


def test_an_id_a_kept_row_has_is_not_given_to_another_decision_of_its_turn() -> None:
    """Two decisions of one long turn: the first has the turn's plain id. When a
    kept row has that id and holds the first, the second keeps the id it had."""
    read = [
        ClassifiedUtterance(
            id="utt_1#1", kind=UtteranceKind.DECISION, confidence=0.9, text=LOGIN, part_of="utt_1"
        ),
        ClassifiedUtterance(
            id="utt_1#2", kind=UtteranceKind.DECISION, confidence=0.9, text=PRICE, part_of="utt_1"
        ),
    ]
    both = [id_ for id_, _ in identified("mtg_1", group_decisions(read), read)]
    assert both[0] == decision_id("mtg_1", ["utt_1"])

    left = identified("mtg_1", group_decisions(read, held={"utt_1#1"}), read, taken={both[0]})

    assert [id_ for id_, _ in left] == [both[1]]


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


def test_a_settling_row_that_only_agrees_takes_its_substance_from_the_proposal() -> None:
    """ "그럼 그렇게 하죠" is true and says nothing. The record needs the turn it agreed to.

    This is the shape the register work found in real meetings: the decision is
    reached by assent, so the row carrying ``settles`` is the shortest one in the
    region.
    """
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text="검색 정렬은 인기순으로 바꾸시죠"),
            utterance("utt_2", UtteranceKind.DECISION, text="그럼 그렇게 하죠"),
        ]
    )

    assert groups[0].statement == "검색 정렬은 인기순으로 바꾸시죠"
    assert groups[0].source_utterance_ids == ("utt_1", "utt_2")


def test_the_statement_carries_the_owner_who_took_it_on() -> None:
    """The person is named in a commitment between the decision rows, not in them."""
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text="검색 정렬은 인기순으로 바꾸시죠"),
            utterance("utt_2", UtteranceKind.COMMITMENT, text="제가 볼게요", speaker="박지영"),
            utterance("utt_3", UtteranceKind.DECISION, text="그럼 그렇게 하죠"),
        ]
    )

    assert groups[0].statement == "검색 정렬은 인기순으로 바꾸시죠 (담당 박지영)"


def test_the_statement_ends_in_the_noun_form_and_keeps_its_owner_and_deadline() -> None:
    """The tidied line is what a person confirms; the owner and deadline are added
    after it, so they are never rewritten."""
    groups = group_decisions(
        [
            utterance(
                "utt_1",
                UtteranceKind.DECISION,
                text="그럼 검색 정렬은 인기순으로 진행합시다",
            ),
            utterance("utt_2", UtteranceKind.COMMITMENT, text="제가 볼게요", speaker="박지영"),
            utterance("utt_3", UtteranceKind.DECISION, text="네 그렇게 하죠"),
        ]
    )

    assert groups[0].statement == "검색 정렬은 인기순으로 진행함 (담당 박지영)"


def test_somebody_handed_the_work_by_name_is_the_owner() -> None:
    """Nobody said "I will"; the decision itself names who does it."""
    groups = group_decisions(
        [utterance("utt_1", UtteranceKind.DECISION, text="배너 시안은 지영 씨가 저번처럼 해주세요")]
    )

    assert groups[0].statement == "배너 시안은 지영 씨가 저번처럼 해주세요 (담당 지영)"


@pytest.mark.parametrize(
    "text",
    [
        "날씨가 안 좋으니 행사는 실내로 옮겨 주세요",
        "고객님이 원하시니 환불 정책은 그대로 두시죠",
    ],
)
def test_a_word_that_only_looks_like_a_name_is_not_an_owner(text: str) -> None:
    """ "날씨가" is not a person, and a customer who is mentioned was not handed anything."""
    groups = group_decisions([utterance("utt_1", UtteranceKind.DECISION, text=text)])

    assert groups[0].statement == text


def test_a_deadline_said_between_the_decision_rows_reaches_the_statement() -> None:
    """ "다음 주 금요일" is only a date once the meeting's own day is known."""
    groups = group_decisions(
        [
            utterance("utt_1", UtteranceKind.DECISION, text="검색 정렬은 인기순으로 바꾸시죠"),
            utterance("utt_2", CHAT, text="다음 주 금요일까지 하면 될까요"),
            utterance("utt_3", UtteranceKind.DECISION, text="그럼 그렇게 하죠"),
        ],
        day=date(2026, 9, 21),
    )

    assert groups[0].statement == "검색 정렬은 인기순으로 바꾸시죠 (기한 2026-10-02)"


def test_a_decision_nobody_was_given_and_nobody_dated_stays_a_plain_sentence() -> None:
    """Most decisions are this. An owner invented to fill the field would be a lie."""
    groups = group_decisions(
        [utterance("utt_1", UtteranceKind.DECISION, text="검색 정렬은 인기순으로 바꾸시죠")]
    )

    assert groups[0].statement == "검색 정렬은 인기순으로 바꾸시죠"


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
        DecisionGroup(
            statement="가자",
            source_utterance_ids=("utt_1",),
            confidence=0.9,
            original_statement="가자",
            core_text="가자",
            substance_id="utt_1",
        )
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


def test_a_decision_id_is_derived_from_where_it_was_settled() -> None:
    """#171: the same meeting and sources give the same id on every rebuild, in
    the same shape ``new_id`` mints."""
    derived = decision_id("mtg_1", ("utt_3", "utt_4"))

    assert derived == decision_id("mtg_1", ("utt_3", "utt_4"))
    assert re.fullmatch(r"dec_[0-9a-f]{32}", derived)
    assert len(derived) == len(mint_id())


@pytest.mark.parametrize(
    ("meeting_id", "sources"),
    [
        ("mtg_1", ("utt_3",)),  # one source fewer
        ("mtg_1", ("utt_3", "utt_5")),  # one source different
        ("mtg_1", ("utt_4", "utt_3")),  # same sources, other order
        ("mtg_2", ("utt_3", "utt_4")),  # same sources, other meeting
        ("mtg_1", ("utt_3\x1futt_4",)),  # one id that contains the separator
    ],
)
def test_a_decision_settled_elsewhere_is_a_different_id(
    meeting_id: str, sources: tuple[str, ...]
) -> None:
    assert decision_id(meeting_id, sources) != decision_id("mtg_1", ("utt_3", "utt_4"))


# --- what the tables must not hold ------------------------------------------


def test_a_decision_belongs_to_the_meeting_and_carries_no_owner() -> None:
    """ADR 0007: reachable by ``meeting_id``, so it survives a departure.

    A decision is the clearest case — the team is still bound by it after the
    person who proposed it leaves. Asserting the whole column set rather than the
    absence of one name means an owner-shaped column added later fails here,
    where the reason is written down.
    """
    columns = {column.name for column in ExtDecision.__table__.columns}

    # ``origin`` says *whether* a person added the decision (``user``) or the
    # model proposed it, never *which* person -- the same flag action items carry.
    assert columns == {
        "id",
        "meeting_id",
        "statement",
        "original_statement",
        # Whether a model wrote the statement or it is the line tidied: about
        # the text, as ``origin`` is, and about no person.
        "statement_resolved",
        "source_digest",
        "needs_recheck",
        "confidence",
        "origin",
        "project_id",
        "project_by_person",
        "created_at",
        "updated_at",
    }


def test_decision_sources_hold_a_link_a_position_and_where_the_part_is() -> None:
    """No copy of the utterance text.

    The quotation is read by joining ``utterances``, so a deleted meeting takes
    it along. Denormalising the text here would leave transcript content behind a
    cascade that no longer reaches it. The part a decision was made from is two
    numbers into that text for the same reason, and never the words.
    """
    columns = ExtDecisionSource.__table__.columns

    assert {column.name for column in columns} == {
        "id",
        "decision_id",
        "utterance_id",
        "position",
        "excerpt_start",
        "excerpt_end",
    }
    assert isinstance(columns["excerpt_start"].type, Integer)
    assert isinstance(columns["excerpt_end"].type, Integer)


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
