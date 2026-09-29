"""The pure parts of decision-lineage building: change classification and matching."""

from __future__ import annotations

import pytest

from autune_context.pipeline.base import NliScores
from autune_context.pipeline.change import (
    adds_condition,
    classify_change,
    marks_reversal,
    strip_keep_words,
)
from autune_context.service import (
    _assign_decisions_to_threads,
    _cosine,
    _ThreadHead,
)
from autune_contracts import ChangeType


def _nli(e: float, n: float, c: float) -> NliScores:
    label = max((("entailment", e), ("neutral", n), ("contradiction", c)), key=lambda p: p[1])[0]
    return NliScores(label=label, entailment=e, contradiction=c, neutral=n)


def test_entailment_in_either_direction_is_unchanged() -> None:
    """A paraphrase with an extra clause is neutral forward but entailed
    backward; it is still the same decision."""
    change, score = classify_change(
        _nli(0.01, 0.99, 0.0),
        _nli(0.99, 0.01, 0.0),
        "주말 직전인 금요일에는 운영 반영을 금지하는 것으로 정리했다.",
        "금요일에는 배포하지 않기로 했다.",
    )
    assert change is ChangeType.UNCHANGED
    assert score == 0.99


def test_a_backward_only_entailment_that_adds_a_condition_is_modified() -> None:
    change, _ = classify_change(
        _nli(0.0, 0.99, 0.0),
        _nli(0.99, 0.01, 0.0),
        "주 2회 재택근무는 입사 3개월이 지난 직원에게만 허용하기로 했다.",
        "재택근무는 주 2회 허용하기로 했다.",
    )
    assert change is ChangeType.MODIFIED


def test_reaffirming_a_changed_state_is_unchanged() -> None:
    """After a switch, "keep using the new one" contradicts the act of
    switching; with the keep-words stripped, NLI sees it entailed."""
    change, score = classify_change(
        _nli(0.14, 0.01, 0.85),
        _nli(0.40, 0.56, 0.05),
        "결제 대행사는 B사를 계속 쓰기로 했다.",
        "결제 대행사를 A사 대신 B사로 바꾸기로 했다.",
        kept_forward=_nli(0.99, 0.01, 0.0),
    )
    assert change is ChangeType.UNCHANGED
    assert score == 0.99


def test_a_contradiction_with_a_negation_is_reversed() -> None:
    change, _ = classify_change(
        _nli(0.0, 0.0, 1.0),
        _nli(0.0, 0.0, 1.0),
        "컨퍼런스는 열지 않기로 했다.",
        "컨퍼런스를 열기로 했다.",
    )
    assert change is ChangeType.REVERSED


def test_a_contradiction_without_one_is_a_moved_parameter() -> None:
    """A date pushed back contradicts the old date just as hard as a
    cancellation does; only the wording tells them apart."""
    change, score = classify_change(
        _nli(0.0, 0.02, 0.98),
        _nli(0.0, 0.01, 0.99),
        "출시일을 3월 29일로 미루기로 했다.",
        "출시일을 3월 15일로 정했다.",
    )
    assert change is ChangeType.MODIFIED
    assert score == 0.99


def test_neither_entailed_nor_contradicted_is_modified() -> None:
    change, score = classify_change(_nli(0.1, 0.8, 0.1), _nli(0.2, 0.7, 0.1), "문장", "문장")
    assert change is ChangeType.MODIFIED
    assert score == 0.8


@pytest.mark.parametrize(
    "statement",
    [
        "베타 없이 바로 출시하기로 했다.",
        "해커톤은 더 이상 진행하지 않기로 했다.",
        "검색 정렬은 최신순으로 안 한다",
        "MySQL 대신 PostgreSQL로 가기로 했다.",
        "판교가 아니라 성수로 옮기기로 했다.",
        "기능 개발은 무기한 보류하기로 했다.",
        "무료 체험 제공은 취소하기로 했다.",
        "이전 계획은 백지화하기로 했다.",  # 백지 + 화, joined
        "모노레포를 접고 저장소를 나누기로 했다.",
        "이번 달로 끝내고 그만두기로 했다.",
        "배포는 하지 말기로 했다.",
        "재시도 없이 폴백으로 처리하기로 했다.",  # A 없이 B: a replacement
        "없던 일로 하기로 했다.",
    ],
)
def test_negation_and_cancel_words_mark_a_reversal(statement: str) -> None:
    assert marks_reversal(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "정식 출시일을 3월 29일로 미루기로 했다.",
        "예산 증액 폭을 10%로 줄이기로 했다.",
        "담당을 백엔드 팀으로 변경하기로 했다.",
    ],
)
def test_a_moved_parameter_is_not_a_reversal(statement: str) -> None:
    assert not marks_reversal(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "배포는 차질 없이 다음 주 화요일로 옮기기로 했다.",
        "예외 없이 모든 PR은 두 명 승인 후 머지하기로 했다.",
        "추가 비용 없이 인원을 5명으로 늘리기로 했다.",
        "추가 예산 없이 10월 출시로 옮기기로 했다.",
        "문제없이 일정대로 진행하기로 했다.",  # 문제 + 없이, one word
        "차질이 없도록 일정을 다음 주로 옮기기로 했다.",  # 없- after a subject particle
    ],
)
def test_an_absence_that_means_smoothly_is_not_a_reversal(statement: str) -> None:
    assert not marks_reversal(statement)


def test_a_smooth_absence_does_not_hide_a_real_negation() -> None:
    assert marks_reversal("차질 없이 정리하고 이번 분기에는 출시하지 않기로 했다.")


def test_an_owner_replaced_with_daesin_reads_as_a_reversal_a_known_limit() -> None:
    """Pinned so a change here is a decision: by the eval set's definitions
    a new owner is ``modified``, but vocabulary cannot tell a person from a
    vendor after 대신 (see ``pipeline.change``)."""
    assert marks_reversal("김민경 대신 강민구가 맡기로 했다.")


def test_cosine_is_1_for_identical_and_0_for_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [2.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 5.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector, no div by zero


def _head(thread_id: str, vector: list[float]) -> _ThreadHead:
    return _ThreadHead(thread_id, vector)


def test_assign_picks_the_most_similar_above_the_threshold() -> None:
    heads = [_head("thr_a", [1.0, 0.0]), _head("thr_b", [0.0, 1.0])]
    assignment = _assign_decisions_to_threads([[0.9, 0.1]], heads, threshold=0.6)
    assert assignment[0].thread_id == "thr_a"


def test_assign_leaves_a_decision_unassigned_below_the_threshold() -> None:
    heads = [_head("thr_a", [1.0, 0.0])]
    assert _assign_decisions_to_threads([[0.0, 1.0]], heads, threshold=0.6) == {}


def test_assign_is_one_to_one() -> None:
    # Both decisions are an equally strong match for both threads; every valid
    # assignment must still be a bijection — no thread taken twice.
    heads = [_head("thr_a", [1.0, 0.0]), _head("thr_b", [1.0, 0.0])]
    vectors = [[1.0, 0.0], [1.0, 0.0]]
    assignment = _assign_decisions_to_threads(vectors, heads, threshold=0.6)
    assert len(assignment) == 2
    assert {head.thread_id for head in assignment.values()} == {"thr_a", "thr_b"}


def test_assign_treats_duplicate_thread_id_entries_as_one_slot() -> None:
    """``heads`` can list the same ``thread_id`` twice — once as the team-wide
    head ``_thread_heads`` found, once as a reprocessed meeting's own
    about-to-be-replaced version for that same thread
    (``build_decision_lineage``). The two entries are candidates for one slot,
    not two: two different decisions must not both land on ``thr_a`` just
    because it appears twice in ``heads``."""
    heads = [
        _head("thr_a", [1.0, 0.0]),  # e.g. the team-wide head
        _head("thr_a", [0.0, 1.0]),  # e.g. this meeting's own old version
    ]
    vectors = [[1.0, 0.0], [0.0, 1.0]]  # two decisions, each a perfect match for one entry
    assignment = _assign_decisions_to_threads(vectors, heads, threshold=0.6)
    assert len(assignment) == 1  # only one decision can take thr_a, not both


def test_assign_prefers_the_stronger_match_regardless_of_input_order() -> None:
    """The bug this replaces: a weak match earlier in the decision list could
    grab a thread out from under a much stronger match later in it. With only
    one candidate thread, the weak decision must lose it either way."""
    heads = [_head("thr_a", [1.0, 0.0])]
    strong = [0.99, 0.01]  # cosine ~0.9999 to thr_a
    weak = [0.7, 0.71]  # cosine ~0.70 to thr_a — still clears a 0.6 threshold

    weak_first = _assign_decisions_to_threads([weak, strong], heads, threshold=0.6)
    assert weak_first.get(0) is None
    assert weak_first[1].thread_id == "thr_a"

    strong_first = _assign_decisions_to_threads([strong, weak], heads, threshold=0.6)
    assert strong_first[0].thread_id == "thr_a"
    assert strong_first.get(1) is None


def test_keep_words_are_stripped_and_keep_the_value() -> None:
    assert strip_keep_words("요청 제한은 분당 300회를 그대로 유지하기로 했다.") == (
        "요청 제한은 분당 300회를 하기로 했다."
    )
    assert strip_keep_words("결제 대행사는 B사를 계속 쓰기로 했다.") is not None
    assert strip_keep_words("출시일을 3월 29일로 미루기로 했다.") is None


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        ("재택은 입사 3개월 지난 직원에게만 허용한다.", "재택은 주 2회 허용한다.", True),
        ("QA 승인과 함께 보안 점검도 받는다.", "배포 전에 QA 승인을 받는다.", True),
        ("대상을 외부 고객 50명까지 넓힌다.", "대상은 사내 직원이다.", True),
        ("iOS 16 이상만 지원한다.", "최소 지원 버전은 iOS 16이다.", False),  # a bound, restated
        ("운영 DB에는 DBA만 접근한다.", "운영 DB 접근은 DBA에게만 허용한다.", False),  # not new
    ],
)
def test_adds_condition(current: str, previous: str, expected: bool) -> None:
    assert adds_condition(current, previous) is expected
