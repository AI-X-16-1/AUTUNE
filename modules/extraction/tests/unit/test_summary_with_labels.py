"""The classifier's answer carries a one-line summary of each commitment and
decision, and that line is what the item and the decision show (the user,
2026-10-06: summarise in the same call that extracts).

Before, every commitment and many decisions cost a request of their own to the
resolver, which rewrites one sentence and hands a longer target back
unchanged. No network: a fake provider answers the classifier's request, and
the task runs for real with a resolver that records what it is asked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts.enums import UtteranceKind
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
)
from autune_contracts.transcript import Utterance as SpokenLine
from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance
from autune_extraction import tasks
from autune_extraction.decisions import ClassifiedUtterance, decision_id, in_pieces
from autune_extraction.models import ExtActionItem, ExtDecision
from autune_extraction.pipeline import FakeClassifier, FakeNli, Prediction
from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline.llm import (
    SUMMARY_MAX_CHARS,
    LlmClassifier,
    _prediction,
    parse_summaries,
    usable_summary,
)

K = UtteranceKind
MEETING = "mtg_1"

PROMISE = "그건 제가 금요일까지 정리해서 공유드릴게요"
DECIDED = "그럼 그렇게 가기로 했습니다"
CHAT = "오늘 날씨가 참 좋네요"


# --- the classifier's side -------------------------------------------------------------


class Provider:
    """Answers like ``generateContent`` with the labels and summaries a test
    gives it, keyed by what a target line ends with."""

    def __init__(self, labels: dict[str, str], summaries: dict[str, str]) -> None:
        self.labels = labels
        self.summaries = summaries
        self.texts: list[str] = []

    def request(self, method: str, path: str, *, json: dict) -> dict:  # noqa: A002 - httpx's name
        text = json["contents"][0]["parts"][0]["text"]
        self.texts.append(text)
        labels: dict[str, str] = {}
        summaries: dict[str, str] = {}
        for line in text.splitlines():
            m = re.match(r"(\d+) \[(대상|문맥)\] (.*)", line)
            if not m:
                continue
            for ending, kind in self.labels.items():
                if m[3].endswith(ending):
                    labels[m[1]] = kind
            for ending, summary in self.summaries.items():
                if m[3].endswith(ending):
                    summaries[m[1]] = summary
        answer = _dumps({"labels": labels, "summaries": summaries})
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}


def _dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def classify(
    texts: list[str],
    labels: dict[str, str],
    summaries: dict[str, str],
    roster: tuple[str, ...] = (),
) -> tuple[list[Prediction], Provider]:
    provider = Provider(labels, summaries)
    c = LlmClassifier(api_key="k", model="gemini-test", base_url="http://llm.invalid")
    c._client = provider  # type: ignore[assignment]
    c.use_roster(roster)
    return c.classify(texts), provider


def test_a_commitment_and_a_decision_come_back_with_the_line_written_for_them() -> None:
    with capture_logs() as logs:
        (chat, promise, decided), _ = classify(
            ["결제 화면 시안 얘기를 해 보죠", PROMISE, DECIDED],
            {"공유드릴게요": "commitment", "했습니다": "decision"},
            {
                "공유드릴게요": "결제 화면 시안을 금요일까지 정리해 공유",
                "했습니다": "결제 화면 시안대로 진행하기로 함",
                "보죠": "잡담에 붙인 요약",
            },
        )

    assert promise.summary == "결제 화면 시안을 금요일까지 정리해 공유"
    assert decided.summary == "결제 화면 시안대로 진행하기로 함"
    # A line with no label that takes a summary has none, whatever came back.
    assert chat.kind is None and chat.summary == ""
    (entry,) = [e for e in logs if e["event"] == "extraction_llm_classified"]
    assert entry["summarised"] == 2
    assert "결제" not in repr(logs)


def test_the_request_asks_for_the_summary_and_still_fits_the_guard() -> None:
    _, provider = classify([PROMISE] * 40, {}, {})

    assert '"summaries"' in llm_module.INSTRUCTIONS
    assert len(llm_module.INSTRUCTIONS) + max(len(t) for t in provider.texts) < 4000


def test_a_summary_for_a_kind_that_takes_none_is_ignored() -> None:
    (worry,), _ = classify(
        ["그 일정이면 너무 빠듯해서 걱정이에요"],
        {"걱정이에요": "concern"},
        {"걱정이에요": "일정이 빠듯하다는 우려"},
    )

    assert worry.kind is K.CONCERN and worry.summary == ""


def test_a_name_in_a_summary_is_the_name_as_said_not_the_placeholder() -> None:
    (promise,), provider = classify(
        ["김민경 님 자료는 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": "[사람1] 님 자료를 금요일까지 정리"},
        roster=("김민경",),
    )

    assert "김민경" not in provider.texts[0] and "[사람1]" in provider.texts[0]
    assert promise.summary == "김민경 님 자료를 금요일까지 정리"


@pytest.mark.parametrize(
    "written",
    [
        "[사람7] 님 자료를 금요일까지 정리",  # a person the request never held
        "자료를 12일까지 정리",  # a number the lines never said
        "자료를 정리 " * 30,  # not a line
        "   ",
    ],
)
def test_a_summary_with_something_the_lines_did_not_say_is_dropped(written: str) -> None:
    (promise,), _ = classify(
        ["김민경 님 자료는 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": written},
        roster=("김민경",),
    )

    assert promise.kind is K.COMMITMENT  # the label stands; only the line is dropped
    assert promise.summary == ""


def test_a_number_said_in_the_lines_before_may_be_in_the_summary() -> None:
    (_context, promise), _ = classify(
        ["시안은 3안까지 나왔어요", "그건 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": "시안 3안을 금요일까지 정리"},
    )

    assert promise.summary == "시안 3안을 금요일까지 정리"


def test_each_piece_of_a_long_turn_has_its_own_summary() -> None:
    filler = "지난주에 시안을 몇 개 돌려 봤는데 반응이 생각보다 갈렸어요."
    turn = " ".join([filler] * 8 + ["그래서 시안은 제가 금요일까지 정리할게요."] + [filler] * 3)

    (prediction,), _ = classify(
        [turn], {"정리할게요.": "commitment"}, {"정리할게요.": "시안을 금요일까지 정리"}
    )

    assert len(prediction.pieces) == len(prediction.piece_summaries) > 1
    assert prediction.summary == ""  # the turn is read in pieces; the lines are theirs
    assert [
        summary
        for (_text, kind), summary in zip(
            prediction.pieces, prediction.piece_summaries, strict=True
        )
        if kind is K.COMMITMENT
    ] == ["시안을 금요일까지 정리"]
    assert [s for s in prediction.piece_summaries if s] == ["시안을 금요일까지 정리"]


def test_parse_summaries_drops_what_it_cannot_read() -> None:
    assert parse_summaries(
        '{"labels": {}, "summaries": {"2": " 두  줄\\n요약 ", "x": "y", "3": 4}}'
    ) == {2: "두 줄 요약"}
    assert parse_summaries('{"labels": {"1": "commitment"}}') == {}
    assert parse_summaries('{"summaries": ["a"]}') == {} and parse_summaries("no json") == {}


def test_usable_summary_is_at_most_a_line() -> None:
    assert usable_summary("가" * SUMMARY_MAX_CHARS, {}, "") == "가" * SUMMARY_MAX_CHARS
    assert usable_summary("가" * (SUMMARY_MAX_CHARS + 1), {}, "") == ""
    assert usable_summary(' "따옴표로 감싼 요약" ', {}, "") == "따옴표로 감싼 요약"


# --- through the task --------------------------------------------------------------------


class Written(FakeClassifier):
    """The fake classifier, with the line the cloud one would have written for
    the texts a test names."""

    lines: dict[str, str] = {}

    def classify(self, texts: list[str]) -> list[Prediction]:
        return [
            _prediction(p.kind, summary=type(self).lines.get(text, ""))
            for text, p in zip(texts, super().classify(texts), strict=True)
        ]


class Asked:
    """A resolver that records what it is asked and answers with the target."""

    model_version = "asked"
    targets: list[str] = []

    def resolve(self, requests: list) -> list[str]:
        type(self).targets.extend(r.target for r in requests)
        return [r.target for r in requests]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        session.add(Participant(id="par_1", meeting_id=MEETING, speaker_label="A", consented=True))
        session.commit()

        @contextmanager
        def scope() -> Iterator[Session]:
            yield session
            session.commit()

        Written.lines = {}
        Asked.targets = []
        monkeypatch.setattr(tasks, "session_scope", scope)
        monkeypatch.setattr(tasks, "get_classifier", Written)
        monkeypatch.setattr(tasks, "get_nli", FakeNli)
        monkeypatch.setattr(tasks, "get_resolver", Asked)
        monkeypatch.setattr(tasks, "publish", lambda event, payload: [])
        yield session


def meeting(session: Session, *lines: str) -> dict:
    spoken = []
    for number, text in enumerate(lines, start=1):
        session.add(
            Utterance(
                id=f"utt_{number}",
                meeting_id=MEETING,
                participant_id="par_1",
                speaker_label="SPEAKER_00",
                start_sec=number * 10.0,
                end_sec=number * 10.0 + 5.0,
                text=text,
                confidence=0.9,
            )
        )
        spoken.append(
            SpokenLine(
                id=f"utt_{number}",
                speaker="Speaker 1",
                start=number * 10.0,
                end=number * 10.0 + 5.0,
                text=text,
                confidence=0.9,
            )
        )
    session.commit()
    return TranscriptReady(
        meeting_id=MEETING,
        utterances=spoken,
        metadata=TranscriptMetadata(
            duration=60.0,
            source=next(iter(TranscriptSource)),
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    ).model_dump(mode="json")


SAID_PROMISE = "그건 제가 금요일까지 정리하겠습니다"
OTHER_PROMISE = "견적은 제가 수요일까지 받겠습니다"
SAID_DECISION = "그럼 그렇게 가기로 했습니다"


def test_an_item_shows_the_line_written_with_its_label_and_the_resolver_is_not_asked(
    wired: Session,
) -> None:
    Written.lines = {SAID_PROMISE: "결제 화면 시안을 금요일까지 정리"}

    tasks.on_transcript_ready(meeting(wired, CHAT, SAID_PROMISE, OTHER_PROMISE))

    items = {i.due_text: i for i in wired.query(ExtActionItem)}
    assert items["금요일"].description == "결제 화면 시안을 금요일까지 정리"
    assert items["금요일"].description_resolved is True
    assert [s.utterance_id for s in items["금요일"].sources] == ["utt_2"]
    # The one with no line written for it is the resolver's, as before.
    assert Asked.targets == [OTHER_PROMISE]
    assert items["수요일"].description_resolved is False


def test_a_decision_shows_the_line_written_with_its_label_and_keeps_its_id(
    wired: Session,
) -> None:
    Written.lines = {SAID_DECISION: "결제 화면은 시안대로 진행"}

    tasks.on_transcript_ready(meeting(wired, CHAT, SAID_DECISION))

    (decision,) = wired.query(ExtDecision).all()
    assert decision.statement == "결제 화면은 시안대로 진행"
    # What module D is sent is still the sentence as said.
    assert decision.original_statement == SAID_DECISION
    assert decision.id == decision_id(MEETING, ["utt_2"])
    assert Asked.targets == []


def test_with_no_line_written_everything_is_as_it_was(wired: Session) -> None:
    tasks.on_transcript_ready(meeting(wired, CHAT, SAID_PROMISE, SAID_DECISION))

    (item,) = wired.query(ExtActionItem).all()
    (decision,) = wired.query(ExtDecision).all()
    assert item.description_resolved is False and Asked.targets == [SAID_PROMISE]
    assert decision.statement == "그렇게 가기로 함"


def test_a_piece_read_as_a_line_carries_the_summary_written_for_it() -> None:
    turn = ClassifiedUtterance(
        id="utt_1",
        kind=K.COMMITMENT,
        confidence=0.9,
        text="",
        pieces=((CHAT, None), (PROMISE, K.COMMITMENT)),
        piece_summaries=("", "시안을 금요일까지 정리"),
    )

    read = in_pieces([turn])

    assert [(u.id, u.summary) for u in read] == [
        ("utt_1#1", ""),
        ("utt_1#2", "시안을 금요일까지 정리"),
    ]
    assert all(u.piece_summaries == () for u in read)
