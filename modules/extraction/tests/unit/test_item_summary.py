"""An item's summary, the lines it says it was written from, and what a person sees.

No network: a fake provider stands in for Gemini. The rules under test: the
candidates come from the whole meeting but only a model that can cite is given
them, the answer maps line numbers back to utterance ids and keeps only real,
other lines, the names never leave and come back, and the drawer reads only what
is still consented and non-blank.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Base, Meeting, Participant, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemRelated,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
)
from autune_extraction.pipeline.base import Resolution, ResolutionRequest
from autune_extraction.pipeline.related import related_ids
from autune_extraction.pipeline.resolver import LlmResolver

# --- the wide net ------------------------------------------------------------------

MEETING_LINES = [
    ("u1", "지난주 결제 로그 필드 하나 더 넣기로 했던 일이 있어요"),
    ("u2", "네"),
    ("u3", "클라에서 갱신이 늦게 되는 버그는 서버 쪽에서는 고쳤어요"),
    ("u4", "점심은 뭘 먹을까요"),
    ("u5", "그 갱신 버그 원인이 캐시라는 얘기도 있었어요"),
    ("u6", "그건 제가 이번 빌드에 넣어 볼게요"),
]


def test_related_lines_are_the_ones_about_the_same_thing_in_spoken_order() -> None:
    found = related_ids("u6", MEETING_LINES, min_score=0.0)

    assert "u6" not in found, "never the target itself"
    order = [line_id for line_id, _ in MEETING_LINES]
    assert found == sorted(found, key=order.index), "spoken order, not score order"


FILLER = [
    (f"f{n}", text)
    for n, text in enumerate(
        [
            "점심 메뉴는 아직 못 정했어요 어떻게 할까요",
            "회의실 예약은 다음 주부터 다시 열린다고 합니다",
            "출장 일정은 아직 확정되지 않았습니다 참고하세요",
            "분기 목표는 지난 회의에서 이미 공유드렸습니다",
            "교육 자료는 다음 달까지 준비하면 충분합니다",
            "휴가 신청은 인사팀 시스템으로 올려 주세요",
            "고객 문의는 오전에 몰리는 경향이 있습니다",
            "예산 집행은 분기 말에 한꺼번에 검토합니다",
            "신규 입사자 온보딩은 이번 주에 시작합니다",
            "주간 보고는 금요일 오후에 취합하고 있습니다",
            "장비 구매 요청은 총무팀에서 처리하고 있습니다",
            "사무실 이전 계획은 아직 논의 중인 단계입니다",
        ]
    )
]


def test_a_shared_subject_ranks_above_a_shared_ending() -> None:
    lines = [
        ("a", "클라에서 갱신이 늦게 되는 버그는 서버 쪽에서는 고쳤어요"),
        *FILLER[:6],
        ("c", "그 갱신 버그 원인이 캐시라는 얘기도 있었어요"),
        *FILLER[6:],
        ("t", "그 갱신 버그는 제가 이번 빌드에 넣어 볼게요"),
    ]

    assert related_ids("t", lines, limit=2) == ["a", "c"]
    assert not set(related_ids("t", lines)) & {line_id for line_id, _ in FILLER}


def test_the_window_the_target_and_short_lines_are_left_out() -> None:
    found = related_ids("u6", MEETING_LINES, exclude={"u5"}, min_score=0.0)

    assert "u5" not in found and "u6" not in found and "u2" not in found  # 네 is too short


def test_a_target_with_nothing_in_common_has_no_related_lines() -> None:
    lines = [("a", "점심은 뭘 먹을까요 오늘은"), ("t", "예산안 검토는 내일까지 끝낼게요")]

    assert related_ids("t", lines) == []
    assert related_ids("missing", lines) == []


# --- the request the task builds ---------------------------------------------------


def classified(rows: list[tuple[str, str, UtteranceKind | None]]) -> list[ClassifiedUtterance]:
    return [
        ClassifiedUtterance(id=i, kind=k, confidence=0.9, text=t, speaker="김민경")
        for i, t, k in rows
    ]


class Citing:
    """A resolver that can say which lines it used."""

    model_version = "citing"

    def __init__(self) -> None:
        self.received: list[ResolutionRequest] = []

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        return [r.text for r in self.resolve_with_evidence(requests)]

    def resolve_with_evidence(self, requests: list[ResolutionRequest]) -> list[Resolution]:
        self.received = list(requests)
        return [
            Resolution(r.target + " (정리)", tuple(i for i, _ in r.related[:1])) for r in requests
        ]


class Plain:
    """A resolver that only rewrites."""

    model_version = "plain"

    def __init__(self) -> None:
        self.received: list[ResolutionRequest] = []

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        self.received = list(requests)
        return [r.target for r in requests]


ROWS = [
    ("u1", "결제 로그 필드 하나 더 넣기로 했던 일이 있어요", None),
    ("u2", "클라에서 갱신이 늦게 되는 버그는 서버 쪽에서는 고쳤어요", None),
    *[(f"f{n}", text, None) for n, (_, text) in enumerate(FILLER)],
    ("u4", "네 그렇죠", None),
    ("u5", "그렇군요 알겠습니다", None),
    ("u6", "점심 메뉴는 나중에 정해요 그럼", None),
    ("u7", "오후 일정은 다들 괜찮은가요 오늘", None),
    ("u8", "그 갱신 버그는 제가 이번 빌드에 넣어 볼게요", UtteranceKind.COMMITMENT),
]


def test_a_resolver_that_cites_is_offered_lines_from_outside_the_window() -> None:
    resolver = Citing()

    summaries = service.resolve_commitment_summaries(resolver, classified(ROWS))

    (request,) = resolver.received
    assert request.target_id == "u8"
    assert request.context_ids == ("u4", "u5", "u6", "u7")  # the four before it
    offered = [line_id for line_id, _ in request.related]
    assert "u2" in offered, "a line about the same bug, said far from the commitment"
    assert not set(offered) & {"u4", "u5", "u6", "u7", "u8"}, "never the window or the target"
    assert summaries["u8"].used == (offered[0],)


def test_a_resolver_that_cannot_cite_is_not_handed_the_candidates() -> None:
    resolver = Plain()

    summaries = service.resolve_commitment_summaries(resolver, classified(ROWS))

    (request,) = resolver.received
    assert request.related == ()
    assert summaries["u8"] == Resolution(ROWS[-1][1])


def test_the_references_are_the_summaries_texts() -> None:
    resolver = Citing()

    texts = service.resolve_commitment_references(resolver, classified(ROWS))

    assert texts == {"u8": ROWS[-1][1] + " (정리)"}


def test_a_non_consenting_speakers_line_is_in_neither_the_window_nor_the_candidates() -> None:
    rows = [(i, "" if i == "u2" else t, k) for i, t, k in ROWS]  # u2 blanked, as classify does
    resolver = Citing()

    service.resolve_commitment_summaries(resolver, classified(rows))

    (request,) = resolver.received
    assert "u2" not in [line_id for line_id, _ in request.related]


# --- the model's answer ------------------------------------------------------------


class Provider:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.bodies.append(json)
        answer = self.answers.pop(0) if self.answers else ""
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}

    @property
    def sent(self) -> str:
        return json.dumps(self.bodies, ensure_ascii=False)


def resolver(provider: Provider, *, second: str = "") -> LlmResolver:
    r = LlmResolver(
        api_key="k", model="first", base_url="http://llm.invalid", fallback_model=second
    )
    r._client = provider  # type: ignore[assignment]
    return r


def answer(summary: str, used: list[Any]) -> str:
    return json.dumps({"summary": summary, "used": used}, ensure_ascii=False)


TARGET = "그 갱신 버그는 제가 이번 빌드에 넣어 볼게요"
REQUEST = ResolutionRequest(
    target=TARGET,
    context=("네 그렇죠", "그렇군요 알겠습니다"),
    context_after=("알겠어요",),
    target_id="t",
    context_ids=("c1", "c2"),
    context_after_ids=("a1",),
    related=(("r1", "클라에서 갱신이 늦게 되는 버그는 서버 쪽에서는 고쳤어요"),),
)
SUMMARY = "클라에서 갱신이 늦게 되는 그 갱신 버그는 제가 이번 빌드에 넣어 볼게요"


def test_the_lines_the_model_cites_come_back_as_ids_in_spoken_order() -> None:
    provider = Provider(answer(SUMMARY, [5, 1]))

    (out,) = resolver(provider).resolve_with_evidence([REQUEST])

    assert out == Resolution(SUMMARY, ("c1", "r1"))  # 1 is context, 5 is related; never the target
    body = provider.bodies[0]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    sent = provider.sent
    assert "[관련]" in sent and "[대상]" in sent


@pytest.mark.parametrize(
    "used",
    [[3], [99, 0, -1], ["1"], [True], "all", None],
)
def test_the_target_a_missing_line_and_nonsense_are_never_cited(used: Any) -> None:
    (out,) = resolver(Provider(answer(SUMMARY, used))).resolve_with_evidence([REQUEST])

    assert out.text == SUMMARY
    assert out.used == ()


def test_an_answer_that_is_not_json_is_the_raw_quote() -> None:
    (out,) = resolver(Provider("그 버그 얘기를 정리하면 됩니다")).resolve_with_evidence([REQUEST])

    assert out == Resolution(TARGET)


def test_a_sentence_that_came_back_unchanged_cites_nothing() -> None:
    (out,) = resolver(Provider(answer(TARGET, [4]))).resolve_with_evidence([REQUEST])

    assert out == Resolution(TARGET)


def test_at_most_four_lines_are_kept() -> None:
    many = ResolutionRequest(
        target=TARGET,
        target_id="t",
        related=tuple((f"r{i}", f"갱신 버그 얘기 {i}번째 줄입니다") for i in range(6)),
    )

    (out,) = resolver(Provider(answer(SUMMARY, [2, 3, 4, 5, 6, 7]))).resolve_with_evidence([many])

    assert len(out.used) == 4


def test_a_name_in_a_candidate_never_leaves_and_is_restored_in_the_summary() -> None:
    request = ResolutionRequest(
        target="그건 제가 이번 빌드에 넣어 볼게요",
        target_id="t",
        related=(("r1", "박재경 님이 결제 로그 필드 추가를 제안했어요"),),
    )
    provider = Provider(
        answer("박재경 님이 제안한 결제 로그 필드를 제가 이번 빌드에 넣어 볼게요", [2])
    )
    r = resolver(provider)
    r.use_roster(["박 재경"])

    (out,) = r.resolve_with_evidence([request])

    assert "박재경" not in provider.sent and "[사람1]" in provider.sent
    assert out.text == "박재경 님이 제안한 결제 로그 필드를 제가 이번 빌드에 넣어 볼게요"
    assert out.used == ("r1",)


def test_a_dropped_deadline_goes_to_the_second_model_and_its_citations_are_kept() -> None:
    target = "그럼 제가 다음 주 화요일까지 볼게요"
    request = ResolutionRequest(
        target=target, target_id="t", related=(("r1", "고객 인터뷰 결과 정리"),)
    )
    bad = answer("그럼 제가 고객 인터뷰 결과를 볼게요", [2])
    good = answer("그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요", [2])
    provider = Provider(bad, good)

    (out,) = resolver(provider, second="second").resolve_with_evidence([request])

    assert out == Resolution("그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요", ("r1",))
    assert len(provider.bodies) == 2


def test_a_request_without_ids_is_the_plain_rewrite_it_always_was() -> None:
    plain = ResolutionRequest(target=TARGET, context=("네 그렇죠",))
    provider = Provider(SUMMARY)

    (out,) = resolver(provider).resolve_with_evidence([plain])

    assert out == Resolution(SUMMARY)
    assert "responseMimeType" not in provider.bodies[0]["generationConfig"]


def test_two_lines_merged_into_one_sentence_are_not_a_summary() -> None:
    """A finished sentence with more text after it that the target does not have is
    two lines pasted together -- the raw quote stands, and the second model is
    asked once before that."""
    merged = (
        "패키지 사고 우편함에 안 들어가는 건이요 서버 쪽은 고쳤는데 "
        "그 갱신 버그는 제가 이번 빌드에 넣어 볼게요"
    )
    provider = Provider(answer(merged, [5]), answer(merged, [5]))

    (out,) = resolver(provider, second="second").resolve_with_evidence([REQUEST])

    assert out == Resolution(TARGET)
    assert len(provider.bodies) == 2


def test_a_sentence_the_target_already_ends_twice_is_not_penalised() -> None:
    target = "네 알겠습니다 제가 할게요"
    request = ResolutionRequest(
        target=target, target_id="t", related=(("r1", "점검 공지 작성 건"),)
    )

    (out,) = resolver(
        Provider(answer("네 알겠습니다 점검 공지는 제가 할게요", [2]))
    ).resolve_with_evidence([request])

    assert out == Resolution("네 알겠습니다 점검 공지는 제가 할게요", ("r1",))


# --- what is stored and what a person sees -----------------------------------------

MEETING = "mtg_1"
TABLES = [
    User.__table__,
    Meeting.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtActionItemRelated.__table__,
    ExtEditEvent.__table__,
    ExtConfirmation.__table__,
    ExtExternalRef.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        session.add(
            Participant(id="par_yes", meeting_id=MEETING, speaker_label="김", consented=True)
        )
        session.add(
            Participant(id="par_no", meeting_id=MEETING, speaker_label="박", consented=False)
        )
        session.flush()
        yield session


def say(session: Session, n: int, text: str, who: str = "par_yes") -> None:
    session.add(
        StoredUtterance(
            id=f"utt_{n}",
            meeting_id=MEETING,
            participant_id=who,
            speaker_label="화자",
            start_sec=float(n),
            end_sec=float(n) + 0.5,
            text=text,
        )
    )


def test_the_items_related_lines_are_stored_and_read_in_spoken_order(session: Session) -> None:
    for n, text in enumerate(
        ["첫째 줄", "버그 설명 줄입니다", "다른 줄", "그 버그는 제가 넣어 볼게요"], 1
    ):
        say(session, n, text)
    session.flush()
    spoken = [
        service.TranscriptUtterance(
            id=f"utt_{n}",
            speaker="화자",
            speaker_id=None,
            start=float(n),
            end=n + 0.5,
            text=t,
            confidence=0.9,
        )
        for n, t in enumerate(
            ["첫째 줄", "버그 설명 줄입니다", "다른 줄", "그 버그는 제가 넣어 볼게요"], 1
        )
    ]
    rows = [
        ClassifiedUtterance(id=f"utt_{n}", kind=None, confidence=0.0, text=t.text)
        for n, t in enumerate(spoken[:3], 1)
    ] + [
        ClassifiedUtterance(
            id="utt_4", kind=UtteranceKind.COMMITMENT, confidence=0.9, text=spoken[3].text
        )
    ]

    items = service.build_action_items(
        session,
        meeting_id=MEETING,
        utterances=spoken,
        classified=rows,
        related={"utt_4": ("utt_2", "utt_1", "utt_4", "nonsense")},
    )

    assert items is not None
    (item,) = items
    # the commitment itself and an id that is in no utterance are dropped
    assert sorted(r.utterance_id for r in item.related) == ["utt_1", "utt_2"]
    assert [r.text for r in service.related_utterances(session, item.id)] == [
        "첫째 줄",
        "버그 설명 줄입니다",
    ]


def test_a_rerun_replaces_the_related_lines_with_the_items(session: Session) -> None:
    for n in (1, 2):
        say(session, n, f"{n}번째 줄 내용입니다")
    session.flush()
    spoken = [
        service.TranscriptUtterance(
            id=f"utt_{n}",
            speaker="화자",
            speaker_id=None,
            start=float(n),
            end=n + 0.5,
            text=f"{n}번째 줄 내용입니다",
            confidence=0.9,
        )
        for n in (1, 2)
    ]
    rows = [
        ClassifiedUtterance(id="utt_1", kind=None, confidence=0.0, text=spoken[0].text),
        ClassifiedUtterance(
            id="utt_2", kind=UtteranceKind.COMMITMENT, confidence=0.9, text=spoken[1].text
        ),
    ]

    service.build_action_items(
        session,
        meeting_id=MEETING,
        utterances=spoken,
        classified=rows,
        related={"utt_2": ("utt_1",)},
    )
    service.build_action_items(session, meeting_id=MEETING, utterances=spoken, classified=rows)

    (item,) = session.scalars(select(ExtActionItem))
    assert service.related_utterances(session, item.id) == []


def test_a_related_line_of_a_speaker_who_withdrew_consent_is_not_shown(session: Session) -> None:
    say(session, 1, "동의한 사람의 설명 줄입니다")
    say(session, 2, "동의하지 않은 사람의 줄입니다", who="par_no")
    say(session, 3, "   ")
    say(session, 4, "그 버그는 제가 넣어 볼게요")
    item = ExtActionItem(
        meeting_id=MEETING,
        description="그 버그 넣을 예정",
        confidence=0.9,
        origin="model",
        sources=[ExtActionItemSource(utterance_id="utt_4")],
        related=[ExtActionItemRelated(utterance_id=f"utt_{n}") for n in (1, 2, 3)],
    )
    session.add(item)
    session.flush()

    detail = service.read_detail(session, item)

    assert [r.text for r in detail.related] == ["동의한 사람의 설명 줄입니다"]
