"""The pure parts of decision-lineage building: change classification and matching."""

from __future__ import annotations

import pytest

from autune_context.pipeline.base import NliScores
from autune_context.pipeline.change import (
    adds_condition,
    classify_change,
    marks_replacement,
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
