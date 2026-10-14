"""The pure parts of decision-lineage building: change classification and matching."""

from __future__ import annotations

import pytest

from autune_context import service
from autune_context.pipeline.base import NliScores
from autune_context.pipeline.change import (
    adds_condition,
    classify_change,
    marks_as_before,
    marks_in_house,
    marks_keep,
    marks_replacement,
    marks_reversal,
    marks_withdrawal,
    strip_keep_words,
)
from autune_context.service import (
    _assign_decisions_to_threads,
    _change_hypotheses,
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


def test_neither_entailed_nor_contradicted_but_said_to_stay_is_unchanged() -> None:
    change, score = classify_change(
        _nli(0.0, 0.9, 0.1),
        _nli(0.2, 0.7, 0.1),
        "10시 마감은 지금대로 둡시다.",
        "매장 영업은 밤 10시까지 한다.",
    )
    assert change is ChangeType.UNCHANGED
    assert score == 0.9


def test_a_keep_that_contradicts_is_not_rescued_by_the_keep_cue() -> None:
    """Reaffirming the old value after a switch contradicts the switch; the keep
    cue only speaks where NLI has no opinion."""
    change, _ = classify_change(
        _nli(0.0, 0.1, 0.9),
        _nli(0.0, 0.1, 0.9),
        "A사를 계속 쓰기로 했다.",
        "A사 대신 B사로 바꾸기로 했다.",
    )
    assert change is ChangeType.MODIFIED


@pytest.mark.parametrize(
    "statement",
    [
        "체크아웃은 그대로 11시다.",
        "현행 유지.",
        "요금을 동결한다.",
        "지금대로 둔다.",
        "기존처럼 진행한다.",
        "변동 없다.",
        "일정은 변경 없이 간다.",
        "노선은 바꾸지 않기로 했다.",
        "안 고치기로 했다.",
        "가격은 올리지 말죠.",
        "규칙은 변경하지 않는다.",
    ],
)
def test_a_statement_that_says_nothing_changes_marks_a_keep(statement: str) -> None:
    assert marks_keep(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "한 시간 늘린다.",
        "도입하지 않기로 했다.",
        "취소한다.",
        "목요일로 옮긴다.",
        "그렇게 하기로 했다.",
    ],
)
def test_a_change_or_a_withdrawal_is_not_a_keep(statement: str) -> None:
    assert not marks_keep(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "노선은 바꾸지 않기로 했다.",
        "안 바꾸기로 했다.",
        "건드리지 말죠.",
        "규칙은 변경하지 않는다.",
    ],
)
def test_a_negated_change_verb_is_not_a_reversal(statement: str) -> None:
    assert not marks_reversal(statement)


def test_a_negated_change_verb_does_not_hide_a_real_withdrawal() -> None:
    assert marks_reversal("노선은 바꾸지 않고 심야 운행은 하지 않기로 했다.")


@pytest.mark.parametrize(
    ("statement", "previous", "keep"),
    [
        ("인상 없이 가기로 했다.", "가격을 10% 인상한다.", False),
        ("요금은 인하 없이 간다.", "요금을 5% 인하한다.", False),
        ("인상 없이 가기로 했다.", "가격은 만 원이다.", True),
        ("일정은 변경 없이 간다.", "출시는 3월이다.", True),
    ],
)
def test_no_move_after_that_same_move_is_no_keep(statement: str, previous: str, keep: bool) -> None:
    assert marks_keep(statement, previous) is keep
    neutral, _ = classify_change(_nli(0.0, 0.9, 0.1), _nli(0.0, 0.9, 0.1), statement, previous)
    assert neutral is (ChangeType.UNCHANGED if keep else ChangeType.MODIFIED)


_WITHDRAWN_MOVES = [
    ("가격은 올리지 않기로 했다.", "가격을 10% 올린다."),
    ("인력은 늘리지 않기로 했다.", "인력을 두 명 늘린다."),
    ("요금은 인상하지 않는다.", "요금을 5% 인상한다."),
    ("가격은 안 올리기로 했다.", "가격 인상은 다음 달부터다."),
    ("수수료는 내리지 말죠.", "수수료를 1%로 내린다."),
]


@pytest.mark.parametrize(("statement", "previous"), _WITHDRAWN_MOVES)
def test_a_negated_move_the_earlier_decision_made_withdraws_it(
    statement: str, previous: str
) -> None:
    assert marks_reversal(statement, previous)
    assert not marks_keep(statement, previous)


@pytest.mark.parametrize(("statement", "previous"), _WITHDRAWN_MOVES)
def test_a_withdrawn_move_is_reversed_or_modified_never_unchanged(
    statement: str, previous: str
) -> None:
    contradicted, _ = classify_change(_nli(0.0, 0.1, 0.9), _nli(0.0, 0.1, 0.9), statement, previous)
    neutral, _ = classify_change(_nli(0.0, 0.9, 0.1), _nli(0.0, 0.9, 0.1), statement, previous)
    assert contradicted is ChangeType.REVERSED
    assert neutral is ChangeType.MODIFIED


@pytest.mark.parametrize(
    ("statement", "previous"),
    [
        ("가격은 올리지 말죠.", "가격은 만 원이다."),
        ("가격은 올리지 않기로 했다.", "가격은 올리지 않는다."),
        ("인원은 줄이지 않는다.", "인원을 두 명 늘린다."),
        ("요금은 인상하지 않는다.", "요금을 인상하지 않는다."),
    ],
)
def test_a_negated_move_the_earlier_decision_did_not_make_keeps_it(
    statement: str, previous: str
) -> None:
    assert marks_keep(statement, previous)
    assert not marks_reversal(statement, previous)


@pytest.mark.parametrize(
    "statement",
    [
        "서버 유지보수는 월 2회로 한다.",
        "유지 보수 계약은 연장한다.",
        "계속 논의하다가 회의를 주 2회로 한다.",
        "그대로 두다가 다음 달에 올린다.",
    ],
)
def test_a_keep_word_in_a_compound_or_a_clause_that_gives_way_is_not_a_keep(
    statement: str,
) -> None:
    assert not marks_keep(statement)


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


@pytest.mark.parametrize(
    "statement",
    [
        "사내 독서 모임은 이번 분기로 끝냅시다.",
        "견학 프로그램은 일정에서 뺍시다.",
        "공동 마케팅에서는 빠지죠.",
        "그 업체와는 거래를 끊는다.",
        "정기 배송 사업에서는 손 뗍시다.",
        "야외 상영회는 그만한다.",
        "회원 적립 제도는 종료한다.",
        "2호점은 폐업하기로 했다.",
        "종이 영수증으로 되돌린다.",
        "기존 메신저로 돌아간다.",
        "빼기로 했다.",
        "우리는 공동 구매에서 빠지는 게 낫겠어요.",
    ],
)
def test_leaving_or_going_back_marks_a_withdrawal(statement: str) -> None:
    assert marks_withdrawal(statement)


@pytest.mark.parametrize(
    ("statement", "previous"),
    [
        # a part left out: the decision stands
        ("화요일은 빼고 매일 연다.", ""),
        ("주말반만 빼고 강좌를 연다.", ""),
        # a value as the argument: a parameter moved
        ("매장은 8시에 닫는다.", ""),
        ("인원에서 세 명을 뺀다.", ""),
        ("마감 20분 전에는 주방을 닫죠.", ""),
        ("메뉴는 일곱 개로 정리합시다.", ""),
        # not the last clause, or negated
        ("자료를 정리해서 다음 주에 공유한다.", ""),
        ("그렇게 정리했다.", ""),
        ("논의를 마무리했다.", ""),
        ("빼지 않기로 했다.", ""),
        # the earlier decision already said it, or a keep
        ("주차장은 계속 닫는다.", "주차장은 야간에 닫는다."),
        ("야간 출입문은 그대로 닫아 둔다.", ""),
    ],
)
def test_a_part_left_out_a_moved_value_or_a_restated_exit_is_no_withdrawal(
    statement: str, previous: str
) -> None:
    assert not marks_withdrawal(statement, previous)


@pytest.mark.parametrize(
    ("statement", "previous"),
    [
        ("이전 작업은 이번 달까지 끝낸다.", "데이터베이스를 새 서버로 이전한다."),
        ("개편은 다음 주까지 끝내기로 했다.", "신규 가입 화면을 개편한다."),
        ("점검은 금요일까지 종료하기로 했다.", "전 지점 소방 점검을 한다."),
    ],
)
def test_finishing_by_a_deadline_carries_a_decision_out(statement: str, previous: str) -> None:
    assert not marks_withdrawal(statement, previous)
    change, _ = classify_change(_nli(0.9, 0.05, 0.05), _nli(0.9, 0.05, 0.05), statement, previous)
    assert change is ChangeType.UNCHANGED


def test_a_deadline_in_an_earlier_clause_does_not_stop_a_withdrawal() -> None:
    assert marks_withdrawal("이번 시즌까지만 하고 끝냅시다.", "야간 셔틀을 운행한다.")


def test_a_number_that_names_the_subject_does_not_stop_a_withdrawal() -> None:
    assert marks_withdrawal("24시간 상담 창구는 종료한다.")


def test_a_withdrawal_is_reversed_even_where_nli_reads_entailment() -> None:
    """The shared subject makes NLI entail "X를 끝낸다" from "X를 한다"."""
    change, _ = classify_change(
        _nli(0.02, 0.30, 0.68),
        _nli(0.90, 0.05, 0.05),
        "사내 독서 모임은 이번 분기로 끝냅시다.",
        "사내 독서 모임을 운영한다.",
    )
    assert change is ChangeType.REVERSED


def test_a_withdrawal_said_in_the_statement_counts_when_a_line_is_classified() -> None:
    change, _ = classify_change(
        _nli(0.02, 0.95, 0.03),
        _nli(0.05, 0.90, 0.05),
        "문의가 너무 적어요.",
        "전화 상담을 운영한다.",
        statement="전화 상담은 종료한다.",
    )
    assert change is ChangeType.REVERSED


def test_bringing_in_house_what_was_given_out_is_a_withdrawal() -> None:
    assert marks_in_house("행사 촬영은 우리가 직접 한다.", "행사 촬영은 외부 업체에 맡긴다.")
    assert marks_in_house("급여 정산은 자체 시스템으로 한다.", "급여 정산은 대행사에 위탁한다.")
    assert not marks_in_house("행사 촬영은 우리가 직접 한다.", "행사 촬영은 두 명이 한다.")


@pytest.mark.parametrize(
    ("statement", "previous"),
    [
        ("업체와는 팀장이 직접 통화해서 일정을 잡는다.", "배송은 외부 업체에 맡긴다."),
        ("업체 평가는 내부 기준으로 한다.", "청소는 용역 업체에 맡긴다."),
        ("검수는 우리가 직접 한다.", "번역은 외주로 돌린다."),
    ],
)
def test_doing_other_work_directly_keeps_what_was_given_out(statement: str, previous: str) -> None:
    assert not marks_in_house(statement, previous)
    change, _ = classify_change(_nli(0.9, 0.05, 0.05), _nli(0.9, 0.05, 0.05), statement, previous)
    assert change is ChangeType.UNCHANGED


def test_a_terse_in_house_statement_names_no_other_work() -> None:
    assert marks_in_house("리조트가 직접 한다.", "장비 대여는 렌탈 업체에 위탁한다.")


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("예전처럼 창구에서 접수한다.", True),
        ("원래대로 수기로 적는다.", True),
        ("지금대로 둔다.", False),
        ("현행처럼 간다.", False),
    ],
)
def test_going_back_to_how_things_were_marks_a_return(statement: str, expected: bool) -> None:
    assert marks_as_before(statement) is expected


def test_going_back_reverses_only_where_nli_contradicts() -> None:
    previous = "접수는 온라인으로만 받는다."
    contradicted, _ = classify_change(
        _nli(0.0, 0.1, 0.9), _nli(0.0, 0.1, 0.9), "예전처럼 창구에서 접수한다.", previous
    )
    neutral, _ = classify_change(
        _nli(0.0, 0.9, 0.1), _nli(0.0, 0.9, 0.1), "예전처럼 창구에서 접수한다.", previous
    )
    assert contradicted is ChangeType.REVERSED
    assert neutral is ChangeType.UNCHANGED


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


# --- replacement cue: a swap said without a negation ---------------------------


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("로그 수집은 자체 서버에서 한다.", "로그 수집을 외부 SaaS로 이전한다."),
        ("사내 메신저는 카카오워크를 쓴다.", "사내 메신저를 슬랙으로 교체한다."),
        ("고객 문의는 이메일로만 받는다.", "고객 문의를 챗봇으로 대체한다."),
        ("사무실은 판교에 둔다.", "사무실을 판교에서 성수로 옮긴다."),
        ("서버는 온프레미스에서 운영한다.", "서버 운영을 온프레미스에서 클라우드로 갈아탄다."),
        ("원격 회의는 줌으로 한다.", "원격 회의를 구글 미트로 통일한다."),
        ("야간 모니터링은 사내 인력이 직접 한다.", "야간 모니터링을 외부 관제 업체로 전환한다."),
        ("문서 저장소는 위키를 쓴다.", "문서 저장소를 노션으로 이관한다."),
    ],
)
def test_a_swap_for_a_new_thing_is_a_replacement(previous: str, current: str) -> None:
    assert marks_replacement(current, previous)


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        # a weekday, a time of day, a number, a unit: a parameter is moving
        ("회의실 청소는 매주 월요일에 한다.", "회의실 청소를 매주 수요일로 옮긴다."),
        ("신규 입사자 교육은 오전에 한다.", "신규 입사자 교육을 오후로 옮긴다."),
        ("월 요금은 9천 원이다.", "월 요금을 1만 원으로 바꾼다."),
        ("스프린트는 2주 단위다.", "스프린트를 3주 단위로 바꾼다."),
        ("보고 주기는 월 단위다.", "보고 주기를 주 단위로 전환한다."),
        ("점심시간은 열두 시부터다.", "점심시간을 열한 시 반으로 옮긴다."),
        # an owner: replacing the owner is a moved parameter
        ("점검 담당은 박서연이다.", "점검 담당을 최하준으로 바꾼다."),
        ("고객 응대 책임은 1팀에 있다.", "고객 응대 책임을 2팀으로 이관한다."),
        ("행사 주관은 홍보팀이다.", "행사 주관을 마케팅팀으로 옮긴다."),
        # the target is nothing new
        ("고객 문의는 챗봇으로 받는다.", "고객 문의는 챗봇으로 통일한다."),
        # no target at all, or a noun use of a swap word
        ("배포는 금요일에 한다.", "배포 일정을 이전한다."),
        ("서버 교체 주기는 3년이다.", "서버 교체 주기는 3년으로 유지한다."),
        ("회의는 이전 방식으로 진행한다.", "회의는 이전 방식 그대로 진행한다."),
    ],
)
def test_a_moved_value_an_owner_or_no_new_target_is_not_a_replacement(
    previous: str, current: str
) -> None:
    assert not marks_replacement(current, previous)


def test_a_state_change_said_with_a_swap_verb_reads_as_a_replacement_a_known_limit() -> None:
    """ "무료에서 유료로 전환" is arguably a pricing parameter, but nothing in the
    sentence says so. Pinned so a change to this limit is deliberate."""
    assert marks_replacement("베타를 유료로 전환한다.", "베타는 무료로 운영한다.")


def test_a_contradiction_with_a_replacement_is_reversed() -> None:
    change, score = classify_change(
        _nli(0.0, 0.1, 0.9),
        _nli(0.0, 0.2, 0.8),
        "고객 문의를 챗봇으로 대체한다.",
        "고객 문의는 이메일로만 받는다.",
    )
    assert (change, score) == (ChangeType.REVERSED, pytest.approx(0.9))


def test_neutral_both_ways_with_a_replacement_is_reversed() -> None:
    change, score = classify_change(
        _nli(0.1, 0.7, 0.2),
        _nli(0.1, 0.7, 0.2),
        "원격 회의를 구글 미트로 통일한다.",
        "원격 회의는 줌으로 한다.",
    )
    assert (change, score) == (ChangeType.REVERSED, pytest.approx(0.7))


def test_a_contradiction_that_moves_a_value_stays_modified() -> None:
    change, _ = classify_change(
        _nli(0.0, 0.1, 0.9),
        _nli(0.0, 0.1, 0.9),
        "회의실 청소를 매주 수요일로 옮긴다.",
        "회의실 청소는 매주 월요일에 한다.",
    )
    assert change is ChangeType.MODIFIED


def test_entailment_still_wins_over_a_replacement_cue() -> None:
    change, _ = classify_change(
        _nli(0.9, 0.1, 0.0),
        _nli(0.9, 0.1, 0.0),
        "원격 회의를 구글 미트로 통일한다.",
        "원격 회의를 구글 미트로 통일한다.",
    )
    assert change is ChangeType.UNCHANGED


# --- the two guards that v3 and v4 showed had gaps ------------------------------


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        # kiwipiepy makes 책임자 and 담당자 one token; the owner guard knows them
        ("릴리스 책임자는 한유진이다.", "릴리스 책임자를 서도현으로 교체한다."),
        ("점검 담당자는 박서연이다.", "점검 담당자를 최하준으로 바꾼다."),
        ("행사 주관자는 홍보팀이다.", "행사 주관자를 마케팅팀으로 옮긴다."),
        # a number touching a unit abbreviation is still a quantity
        ("백업 용량은 5기가다.", "백업 용량을 10GB로 바꾼다."),
        ("응답 목표는 500ms다.", "응답 목표를 200ms로 바꾼다."),
    ],
)
def test_the_owner_and_unit_guards_cover_the_agent_noun_and_latin_units(
    previous: str, current: str
) -> None:
    assert not marks_replacement(current, previous)


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        # a digit touching Latin letters that are not a unit is part of a name
        ("배송은 직영으로 한다.", "배송을 3PL 업체로 전환한다."),
        ("모바일 망은 LTE로 쓴다.", "모바일 망을 5G로 전환한다."),
        ("거래는 일반 고객과 한다.", "거래를 B2B 중심으로 전환한다."),
        ("파일 저장은 NAS에 한다.", "파일 저장을 S3로 이전한다."),
        ("배포는 도커로 한다.", "배포를 K8s로 전환한다."),
    ],
)
def test_a_digit_inside_a_name_does_not_make_the_target_a_quantity(
    previous: str, current: str
) -> None:
    assert marks_replacement(current, previous)


def test_the_nominaliser_geolo_reads_as_a_new_target_a_known_limit() -> None:
    """ "…하는 걸로 바꿔요": kiwipiepy tags 걸 (것 + 으로) as a noun here, so the cue sees a
    new thing and fires on what is a moved value. Pinned so a change is deliberate."""
    assert marks_replacement(
        "재고 실사를 반기마다 하는 걸로 바꿔요.", "재고 실사는 분기마다 진행한다."
    )


def test_a_terse_decision_threads_on_its_source_utterances() -> None:
    """Statement to statement the pair is far apart; with both sides' source
    utterances it is close -- the higher of the two decides, at the same threshold."""
    heads = [_ThreadHead("thr_a", [1.0, 0.0], "", context_vector=[0.0, 1.0])]
    assignment = _assign_decisions_to_threads(
        [[0.0, 1.0]], heads, threshold=0.6, context_vectors=[[0.1, 0.99]]
    )
    assert assignment[0].thread_id == "thr_a"


def test_without_source_utterances_the_score_is_the_statements() -> None:
    heads = [_ThreadHead("thr_a", [1.0, 0.0])]
    assert _assign_decisions_to_threads([[0.0, 1.0]], heads, 0.6, [None]) == {}
    assert _assign_decisions_to_threads([[0.0, 1.0]], heads, 0.6) == {}


class _Version:
    def __init__(self, statement: str, ids: list[str] | None = None) -> None:
        self.current_statement = statement
        self.source_utterance_ids = ids or []


class _AxisEmbedder:
    """One axis per subject word."""

    _AXES = ("마감", "배너")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(axis in t) for axis in self._AXES] + [0.05] for t in texts]


def test_a_terse_change_is_classified_on_the_line_closest_to_the_earlier_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = {"u1": "손님이 늘었어요.", "u2": "10시 마감은 지금대로 둡시다."}
    monkeypatch.setattr(service, "_consented_texts", lambda session, ids: lines)
    versions = [_Version("영업 마감은 밤 10시다."), _Version("그대로 두기로 했다.", ["u1", "u2"])]

    assert _change_hypotheses(None, _AxisEmbedder(), versions) == ["10시 마감은 지금대로 둡시다."]  # type: ignore[arg-type]


def test_a_statement_closer_than_its_lines_is_classified_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = {"u1": "배너 얘기도 했어요."}
    monkeypatch.setattr(service, "_consented_texts", lambda session, ids: lines)
    versions = [_Version("영업 마감은 밤 10시다."), _Version("영업 마감을 11시로 늦춘다.", ["u1"])]

    assert _change_hypotheses(None, _AxisEmbedder(), versions) == ["영업 마감을 11시로 늦춘다."]  # type: ignore[arg-type]


def test_with_no_readable_line_every_hypothesis_is_the_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "_consented_texts", lambda session, ids: {})
    versions = [_Version("a"), _Version("b", ["u1"]), _Version("c")]

    assert _change_hypotheses(None, _AxisEmbedder(), versions) == ["b", "c"]  # type: ignore[arg-type]
