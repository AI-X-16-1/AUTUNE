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
from autune_extraction import service, tasks
from autune_extraction.decisions import ClassifiedUtterance, decision_id, in_pieces
from autune_extraction.models import (
    ExtActionItem,
    ExtDecision,
    ExtDecisionRelated,
    ExtDecisionReview,
)
from autune_extraction.pipeline import FakeClassifier, FakeNli, Prediction
from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline.base import Resolution
from autune_extraction.pipeline.llm import (
    SUMMARY_MAX_CHARS,
    LlmClassifier,
    _prediction,
    parse_summaries,
    unquoted,
    usable_summary,
)
from autune_extraction.pipeline.related import drawn_on
from autune_extraction.schemas import DecisionReviewUpdate
from autune_extraction.service import SPEECH_DELETED_TEXT

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


@pytest.mark.parametrize(
    "written",
    [
        "[사람N]는 자료를 금요일까지 정리하겠다고 약속함",  # the instructions' own spelling
        "[사람] 님 자료를 금요일까지 정리",
        "[사람A]와 [사람1] 님 자료를 금요일까지 정리",  # one mark put back, one left
    ],
)
def test_a_summary_with_a_name_mark_that_stands_for_nobody_is_dropped(written: str) -> None:
    (promise,), _ = classify(
        ["김민경 님 자료는 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": written},
        roster=("김민경",),
    )

    assert promise.kind is K.COMMITMENT
    assert promise.summary == ""


@pytest.mark.parametrize(
    "written",
    [
        "[대상] 자료를 금요일까지 정리",  # the marker of a line to judge, as measured
        "[문맥]에서 말한 자료를 금요일까지 정리",
        "[ 대상 ] 자료를 금요일까지 정리",
    ],
)
def test_a_summary_with_the_requests_own_line_marker_is_dropped(written: str) -> None:
    (promise,), _ = classify(
        ["김민경 님 자료는 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": written},
        roster=("김민경",),
    )

    assert promise.kind is K.COMMITMENT  # the label stands; only the line is dropped
    assert promise.summary == ""


@pytest.mark.parametrize(
    "written",
    [
        "발표 대상 고객 목록을 금요일까지 정리",  # the word, not the marker
        "[중요] 자료를 금요일까지 정리",  # another bracket is the meeting's own
        "대상] 자료를 금요일까지 정리",
    ],
)
def test_a_word_that_is_not_the_line_marker_stays(written: str) -> None:
    assert usable_summary(written, {}, "") == written


@pytest.mark.parametrize(
    "written",
    [
        "이거를 금요일까지 정리하겠다고 약속함",
        "그거 금요일까지 정리",
        "저거는 금요일까지 정리해서 공유",
        "이것을 금요일까지 정리",
        "금요일까지 그것도 정리",
        "금요일까지 정리할 것은 이거예요",
        "저것을 금요일까지 정리",
        # Run together with the particle.
        "이건 금요일까지 정리",
        "그건 금요일까지 정리해서 공유",
        "저건 금요일까지 정리",
        "이걸 금요일까지 정리",
        "그걸로 금요일까지 정리",
        "저걸 금요일까지 정리",
        "금요일까지 정리할 건 이게요",
        "그게 금요일까지 정리할 것",
        "저게 금요일까지 정리할 것",
    ],
)
def test_a_summary_that_kept_a_word_that_only_points_is_dropped(written: str) -> None:
    (_, promise), _ = classify(
        ["결제 화면 시안 얘기를 해 보죠", PROMISE],
        {"공유드릴게요": "commitment"},
        {"공유드릴게요": written},
    )

    assert promise.kind is K.COMMITMENT  # the label stands; the resolver is asked for the line
    assert promise.summary == ""


@pytest.mark.parametrize(
    "written",
    [
        "이것저것 정리해서 공유",  # several things, pointing at none
        "시안이거나 초안을 정리해서 공유",  # the end of another word
        "그 시안을 정리해서 공유",  # says what
        "이번 시안을 정리해서 공유",
        "저희 시안을 정리해서 공유",
        "그건물 시안을 정리해서 공유",  # run together, and another word
        "이게임 시안을 정리해서 공유",
    ],
)
def test_a_summary_with_a_word_that_only_looks_like_one_is_kept(written: str) -> None:
    assert usable_summary(written, {}, "") == written


def test_the_request_asks_for_a_summary_without_the_one_who_spoke() -> None:
    # Who promised is the row's owner, which the speaker's label fills; the
    # model is sent no speaker, so a subject it writes names nobody.
    assert "주어로 쓰지 말고" in llm_module.INSTRUCTIONS
    assert "할 일부터 적으세요" in llm_module.INSTRUCTIONS


def test_a_name_that_starts_like_a_pointing_word_is_a_name() -> None:
    (promise,), provider = classify(
        ["이건희 님 자료는 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": "[사람1] 님 자료를 금요일까지 정리"},
        roster=("이건희",),
    )

    assert "이건희" not in provider.texts[0]
    assert promise.summary == "이건희 님 자료를 금요일까지 정리"


def test_a_number_said_in_the_lines_before_may_be_in_the_summary() -> None:
    (_context, promise), _ = classify(
        ["시안은 3안까지 나왔어요", "그건 제가 금요일까지 정리할게요"],
        {"정리할게요": "commitment"},
        {"정리할게요": "시안 3안을 금요일까지 정리"},
    )

    assert promise.summary == "시안 3안을 금요일까지 정리"


def test_a_date_from_another_item_of_the_same_request_is_not_this_ones() -> None:
    """PARK, review of #880: checked against the whole request, an invented
    fact was stopped but another target line's date passed as this line's."""
    filler = ["잡담 하나입니다", "잡담 둘입니다", "잡담 셋입니다", "잡담 넷입니다"]
    texts = ["보고서는 제가 12일까지 낼게요", *filler, "견적은 제가 받아 올게요"]

    predictions, provider = classify(
        texts,
        {"낼게요": "commitment", "올게요": "commitment"},
        {"낼게요": "보고서를 12일까지 제출", "올게요": "견적을 12일까지 받아 옴"},
    )

    assert len(provider.texts) == 1  # one request held both lines
    assert predictions[0].summary == "보고서를 12일까지 제출"
    assert predictions[-1].kind is K.COMMITMENT and predictions[-1].summary == ""


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


@pytest.mark.parametrize(
    ("answer", "sentence"),
    [
        (' "따옴표로 감싼 요약" ', "따옴표로 감싼 요약"),
        ("“따옴표로 감싼 요약”", "따옴표로 감싼 요약"),
        ("'따옴표로 감싼 요약'", "따옴표로 감싼 요약"),
        ('"닫지 않은 따옴표', "닫지 않은 따옴표"),
        ('열지 않은 따옴표"', "열지 않은 따옴표"),
        ("열지 않은 따옴표”", "열지 않은 따옴표"),
        # A phrase the sentence quotes keeps both of its marks.
        ('"처리 중입니다" 문구는 제가 만들게요', '"처리 중입니다" 문구는 제가 만들게요'),
        ('문구는 "처리 중입니다"', '문구는 "처리 중입니다"'),
        ('"즉시 알림"과 "요약 알림"', '"즉시 알림"과 "요약 알림"'),
        ("“처리 중입니다” 문구는 제가 만들게요", "“처리 중입니다” 문구는 제가 만들게요"),
        ("따옴표 없는 문장", "따옴표 없는 문장"),
        ('"', ""),
    ],
)
def test_only_the_marks_around_a_whole_answer_or_left_over_come_off(
    answer: str, sentence: str
) -> None:
    assert unquoted(answer) == sentence


def test_a_classifier_summary_that_starts_with_a_quoted_phrase_keeps_its_marks() -> None:
    written = '"처리 중입니다" 문구는 제가 만들게요'

    assert usable_summary(written, {}, "") == written


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


def test_a_line_whose_summary_kept_the_pointing_word_is_the_resolvers(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cloud() -> LlmClassifier:
        classifier = LlmClassifier(api_key="k", model="gemini-test", base_url="http://llm.invalid")
        classifier._client = Provider(  # type: ignore[assignment]
            {"정리하겠습니다": "commitment", "받겠습니다": "commitment"},
            {
                "정리하겠습니다": "이거를 금요일까지 정리하겠다고 약속함",
                "받겠습니다": "견적을 수요일까지 받기",
            },
        )
        return classifier

    monkeypatch.setattr(tasks, "get_classifier", cloud)

    tasks.on_transcript_ready(meeting(wired, CHAT, SAID_PROMISE, OTHER_PROMISE))

    items = {i.due_text: i for i in wired.query(ExtActionItem)}
    # The summary that named nothing is not the row's sentence: the resolver
    # was asked about that line, and here gave it back as it was said.
    assert Asked.targets == [SAID_PROMISE]
    assert "약속함" not in items["금요일"].description
    assert items["금요일"].description_resolved is False
    # The other line's summary said what, and stays.
    assert items["수요일"].description == "견적을 수요일까지 받기"


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


def test_a_decision_that_already_says_everything_still_shows_the_line_written_for_it(
    wired: Session,
) -> None:
    """The resolver is asked only about a decision whose settling turn leaves
    something out. A line that came with the label is shown for every
    decision: the owner asked for each as one line."""
    complete = "그럼 결제 화면은 A안으로 진행하기로 했습니다"
    Written.lines = {complete: "결제 화면은 A안으로 진행"}

    tasks.on_transcript_ready(meeting(wired, CHAT, complete))

    (decision,) = wired.query(ExtDecision).all()
    assert decision.statement == "결제 화면은 A안으로 진행"
    assert decision.original_statement == complete
    # It says nothing the line itself did not: nothing was drawn on, nothing is cited.
    assert wired.query(ExtDecisionRelated).count() == 0
    assert Asked.targets == []


def test_a_line_written_with_the_label_cites_the_lines_it_took_a_word_from(
    wired: Session,
) -> None:
    """The classifier does not say what it drew on, so an item summarised by it
    stored no related lines and the drawer had nothing under "요약에 쓴 발화"
    (found in review of #880). The summary says it itself: 서버, 비용 and 견적
    are in the line before and not in the promise."""
    asked = "민호 님, 서버 비용 견적은 언제까지 받아 볼 수 있을까요"
    promise = "네, 그건 제가 수요일까지 받아서 올리겠습니다"
    Written.lines = {promise: "서버 비용 견적을 수요일까지 받아서 올림"}

    tasks.on_transcript_ready(meeting(wired, CHAT, asked, promise))

    (item,) = wired.query(ExtActionItem).all()
    assert item.description == "서버 비용 견적을 수요일까지 받아서 올림"
    assert [r.utterance_id for r in item.related] == ["utt_2"]  # not the chat before it
    assert [s.utterance_id for s in item.sources] == ["utt_3"]


def test_a_decision_written_with_the_label_cites_them_too(wired: Session) -> None:
    proposed = "결제 화면은 버튼을 위로 올린 시안으로 가면 어떨까요"
    Written.lines = {SAID_DECISION: "결제 화면은 버튼을 위로 올린 시안으로 진행"}

    tasks.on_transcript_ready(meeting(wired, proposed, SAID_DECISION))

    (decision,) = wired.query(ExtDecision).all()
    assert [
        r.utterance_id for r in wired.query(ExtDecisionRelated).filter_by(decision_id=decision.id)
    ] == ["utt_1"]


# --- the speaker deletes the speech ---------------------------------------------------


def statement(session: Session) -> str:
    session.expire_all()
    (decision,) = session.query(ExtDecision).all()
    return decision.statement


def test_a_decision_line_the_classifier_wrote_stays_when_the_speech_is_deleted(
    wired: Session,
) -> None:
    """The user, 2026-10-06 (on #894): a model's sentence about a decision is the
    team's record, as an item's summary is, and stays. It cites nothing here --
    no line before it lends it a word -- and stays all the same."""
    Written.lines = {SAID_DECISION: "결제 화면은 버튼을 위로 올린 시안으로 진행"}
    tasks.on_transcript_ready(meeting(wired, CHAT, SAID_DECISION))
    (decision,) = wired.query(ExtDecision).all()
    assert wired.query(ExtDecisionRelated).count() == 0
    assert decision.statement_resolved is True

    tasks.forget_deleted_speech("user_1", ["utt_2"])

    assert statement(wired).startswith("결제 화면은 버튼을 위로 올린 시안으로 진행")
    assert wired.query(ExtDecision).one().original_statement is None


def test_it_stays_the_same_way_when_it_cites_a_line(wired: Session) -> None:
    """lsh2217, review of #894: with the citation rule the same sentence stayed
    or went by whether a line before it shared a word. It is one rule now."""
    proposed = "결제 화면은 버튼을 위로 올린 시안으로 가면 어떨까요"
    Written.lines = {SAID_DECISION: "결제 화면은 버튼을 위로 올린 시안으로 진행"}
    tasks.on_transcript_ready(meeting(wired, proposed, SAID_DECISION))
    assert wired.query(ExtDecisionRelated).count() == 1

    tasks.forget_deleted_speech("user_1", ["utt_2"])

    assert statement(wired).startswith("결제 화면은 버튼을 위로 올린 시안으로 진행")


def test_a_decision_that_is_the_line_tidied_reads_the_placeholder(wired: Session) -> None:
    """No model wrote it: the statement is the speaker's own line, and goes."""
    said = "결제 화면은 두 번째 시안으로 가기로 했습니다"
    tasks.on_transcript_ready(meeting(wired, CHAT, said))
    (decision,) = wired.query(ExtDecision).all()
    assert decision.statement_resolved is False

    tasks.forget_deleted_speech("user_1", ["utt_2"])

    assert statement(wired) == SPEECH_DELETED_TEXT


class WritesUp:
    """A resolver that writes a decision up; ``cites`` says whether it names a line."""

    model_version = "writes-up"
    cites = True

    def resolve(self, requests: list) -> list[str]:
        return [r.text for r in self.resolve_with_evidence(requests)]

    def resolve_with_evidence(self, requests: list) -> list[Resolution]:
        used = (lambda r: r.context_ids[-1:]) if self.cites else (lambda r: ())
        return [Resolution("결제 화면을 두 번째 시안으로 진행", used(r)) for r in requests]


@pytest.mark.parametrize("cites", [True, False])
def test_the_resolvers_write_up_stays_too_whether_or_not_it_names_a_line(
    wired: Session, monkeypatch: pytest.MonkeyPatch, cites: bool
) -> None:
    """Before the flag a write-up that named no line was taken for the line
    tidied and replaced. An item's rewrite never depended on that
    (``description_resolved``); a decision's does not either now."""
    monkeypatch.setattr(WritesUp, "cites", cites)
    monkeypatch.setattr(tasks, "get_resolver", WritesUp)
    earlier = "결제 화면 시안이 두 개 나와 있습니다"
    tasks.on_transcript_ready(meeting(wired, earlier, SAID_DECISION))
    (decision,) = wired.query(ExtDecision).all()
    assert decision.statement.startswith("결제 화면을 두 번째 시안으로 진행")
    assert decision.statement_resolved is True
    assert wired.query(ExtDecisionRelated).count() == (1 if cites else 0)

    tasks.forget_deleted_speech("user_1", ["utt_2"])

    assert statement(wired).startswith("결제 화면을 두 번째 시안으로 진행")


def test_a_persons_rewording_stays_while_the_line_under_it_goes(wired: Session) -> None:
    """A person's wording is kept apart from the model's (``ExtDecisionReview``):
    the line they reworded reads the placeholder, what they wrote is what stands."""
    said = "결제 화면은 두 번째 시안으로 가기로 했습니다"
    tasks.on_transcript_ready(meeting(wired, CHAT, said))
    (decision,) = wired.query(ExtDecision).all()
    service.review_decision(
        wired,
        decision,
        DecisionReviewUpdate(status="confirmed", statement="결제 화면은 2안으로 확정"),
    )
    wired.commit()

    tasks.forget_deleted_speech("user_1", ["utt_2"])

    assert statement(wired) == SPEECH_DELETED_TEXT
    review = wired.get(ExtDecisionReview, decision.id)
    assert review is not None and review.statement == "결제 화면은 2안으로 확정"
    wired.refresh(decision)
    assert service._confirmed_statement(decision, review) == "결제 화면은 2안으로 확정"


def test_a_line_with_no_text_does_not_use_up_one_of_the_three() -> None:
    """PARKJAEKYUNG0525, review of #894: a non-consenting speaker's empty line
    was counted among the three before being left out, so the reach was
    shorter than the check's whenever one sat in between."""
    lines = [
        ClassifiedUtterance(
            id="utt_1", text="서버 비용 견적은 언제 나오나요", kind=None, confidence=0.9
        ),
        ClassifiedUtterance(id="utt_2", text="", kind=None, confidence=0.0),
        ClassifiedUtterance(id="utt_3", text="오늘 날씨가 참 좋네요", kind=None, confidence=0.9),
        ClassifiedUtterance(id="utt_4", text="네 그렇네요 정말", kind=None, confidence=0.9),
        ClassifiedUtterance(
            id="utt_5",
            text="그건 제가 금요일까지 정리할게요",
            kind=UtteranceKind.COMMITMENT,
            confidence=0.9,
            summary="서버 견적을 금요일까지 정리",
        ),
    ]

    assert service._written(lines, 4).used == ("utt_1",)


def test_a_word_the_line_already_has_cites_nothing() -> None:
    """Not the wide net: sharing "제가" or the promise's own words is not
    having been drawn on."""
    before = [
        ("utt_1", "제가 지난주에 금요일까지 보고서를 냈어요"),
        ("utt_2", "서버 비용 견적은 언제 나오나요"),
        ("utt_3", ""),
    ]

    assert drawn_on("제가 금요일까지 정리", "제가 금요일까지 정리할게요", before) == []
    assert drawn_on("서버 견적을 금요일까지 정리", "그건 제가 금요일까지 정리할게요", before) == [
        "utt_2"
    ]
    # A particle or an ending does not make a different word: 견적은 in the
    # line is the 견적을 of the summary, both ways.
    assert drawn_on("견적을 정리", "견적은 제가 정리할게요", [("utt_9", "견적이 늦네요")]) == []
    assert drawn_on(
        "견적을 금요일까지 정리",
        "그건 제가 금요일까지 정리할게요",
        [("utt_9", "견적은 언제 나오나요")],
    ) == ["utt_9"]


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


def test_a_line_further_back_than_the_summarys_own_context_is_not_cited(wired: Session) -> None:
    """Only the lines the summary was checked against can be what it drew on."""
    asked = "민호 님, 서버 비용 견적은 언제까지 받아 볼 수 있을까요"
    promise = "네, 그건 제가 수요일까지 받아서 올리겠습니다"
    Written.lines = {promise: "서버 비용 견적을 수요일까지 받아서 올림"}

    tasks.on_transcript_ready(meeting(wired, asked, CHAT, CHAT, CHAT, promise))

    (item,) = wired.query(ExtActionItem).all()
    assert [r.utterance_id for r in item.related] == []


def test_a_rerun_that_brings_a_written_line_marks_the_same_row(wired: Session) -> None:
    """The mark follows the statement on a rerun: the row is the same row
    (same utterances, same id), and what it says changed from the line to a
    model's sentence -- or back."""
    event = meeting(wired, CHAT, SAID_DECISION)
    tasks.on_transcript_ready(event)
    assert wired.query(ExtDecision).one().statement_resolved is False

    Written.lines = {SAID_DECISION: "결제 화면은 버튼을 위로 올린 시안으로 진행"}
    tasks.on_transcript_ready(event)
    wired.expire_all()
    assert wired.query(ExtDecision).one().statement_resolved is True

    Written.lines = {}
    tasks.on_transcript_ready(event)
    wired.expire_all()
    assert wired.query(ExtDecision).one().statement_resolved is False
