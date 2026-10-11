"""The classifier's answer says which words of a line carry the promise or the
decision, and those words -- found in the utterance as it is stored -- are the
part a reader is shown (the user, 2026-10-08: only the part of the utterance
the item is about).

A sentence is as narrow as cutting a turn can get, and an utterance of up to
300 characters is not cut at all. What is tested here is that a model only
ever chooses *where* the quotation is cut: anything it returns that is not in
the line is not used, and the quotation falls back to the sentence, then to
the utterance. No network: a fake provider answers the real classifier's
request and the task runs for real.
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
from autune_extraction.decisions import ClassifiedUtterance, in_pieces
from autune_extraction.excerpt import cut, joined, narrowed, quoted
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtDecision,
    ExtDecisionSource,
)
from autune_extraction.pipeline import FakeNli, Prediction
from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline.llm import (
    LONG_TURN_CHARS,
    LlmClassifier,
    _names,
    parse_parts,
    usable_part,
)

K = UtteranceKind
MEETING = "mtg_1"

CHAT = "오늘 날씨가 참 좋네요"
REASON = "지난번에 고객사에서 일정이 너무 빠듯하다는 말이 있었으니까"
PROMISE = "수정본은 제가 금요일까지 보내겠습니다"
SHORT = f"{REASON} {PROMISE}"
"""One utterance, far under the 300 characters a turn is cut at, of which the
promise is the second half."""

SETTLED = "출시는 다음 달로 미루기로 했습니다"
DECISION = f"여러 의견이 있었지만 정리하면 {SETTLED}"

FILLER = "그 부분은 지난 분기에도 같은 이야기가 나왔던 것으로 기억합니다."
LONG_PROMISE = "비용이 걸리는 일이긴 하지만 견적은 제가 수요일까지 받겠습니다."
LONG = " ".join([FILLER] * 5 + [LONG_PROMISE] + [FILLER] * 4)
"""A turn over the limit: read sentence by sentence, the promise inside one."""
WORDS = "견적은 제가 수요일까지 받겠습니다."


# --- the lookup -----------------------------------------------------------------------


def test_the_words_are_found_where_they_stand_in_the_utterance() -> None:
    span = narrowed(SHORT, None, PROMISE)

    assert span is not None and SHORT[span[0] : span[1]] == PROMISE


def test_spacing_is_not_compared_and_the_cut_is_the_utterances_own_characters() -> None:
    whole = "그러면  수정본은   제가\n금요일까지 보내겠습니다 아마도요"

    span = narrowed(whole, None, "수정본은 제가 금요일까지 보내겠습니다")

    assert span is not None
    assert whole[span[0] : span[1]] == "수정본은   제가\n금요일까지 보내겠습니다"


@pytest.mark.parametrize(
    "words",
    [
        "",
        "   ",
        "수정본을 제가 금요일까지 보내기로 함",  # reworded
        "수정본은 제가 목요일까지 보내겠습니다",  # one word changed
        SHORT,  # all of it
        f"  {SHORT} ",
    ],
)
def test_words_that_are_not_there_or_are_all_of_it_give_no_part(words: str) -> None:
    assert narrowed(SHORT, None, words) is None
    assert quoted(SHORT, None, words) is None


def test_words_are_looked_for_inside_the_sentence_the_line_was() -> None:
    """A phrase said twice in a turn is found where this item was made from."""
    first = "네 제가 하겠습니다 일단은요."
    second = "그리고 보고서도 제가 하겠습니다 금요일까지요."
    whole = f"{first} 중간에 다른 얘기가 있었습니다. {second}"

    span = narrowed(whole, second, "제가 하겠습니다")

    assert span is not None and span[0] > whole.index(second)
    assert whole[span[0] : span[1]] == "제가 하겠습니다"


def test_a_sentence_is_the_part_when_the_words_cannot_be_used() -> None:
    whole = f"{FILLER} {LONG_PROMISE} {FILLER}"

    for words in ("", "견적을 수요일까지 받기로 함", FILLER + "x"):
        span = quoted(whole, LONG_PROMISE, words)
        assert span is not None and whole[span[0] : span[1]] == LONG_PROMISE
    # Words of another sentence of the turn are not this line's.
    span = quoted(whole, LONG_PROMISE, FILLER)
    assert span is not None and whole[span[0] : span[1]] == LONG_PROMISE


def test_several_parts_of_one_utterance_are_one_span_and_a_whole_one_makes_it_whole() -> None:
    whole = f"{FILLER} {LONG_PROMISE} {SETTLED}"
    one = narrowed(whole, None, WORDS)
    other = narrowed(whole, None, SETTLED)

    assert joined(whole, [one, other]) == (one[0], other[1])  # type: ignore[index]
    assert joined(whole, [one, None]) is None
    assert joined(whole, []) is None
    assert joined(whole, [(0, 5), (5, len(whole))]) is None


# --- the classifier's side ------------------------------------------------------------


class Provider:
    """Answers like ``generateContent``: a label, and the part a test gives,
    for the target lines that end with a key."""

    def __init__(self, labels: dict[str, str], parts: dict[str, str]) -> None:
        self.labels = labels
        self.parts = parts
        self.texts: list[str] = []

    def request(self, method: str, path: str, *, json: dict) -> dict:  # noqa: A002 - httpx's name
        text = json["contents"][0]["parts"][0]["text"]
        self.texts.append(text)
        labels: dict[str, str] = {}
        parts: dict[str, str] = {}
        for line in text.splitlines():
            m = re.match(r"(\d+) \[(대상|문맥)\] (.*)", line)
            if not m:
                continue
            for ending, kind in self.labels.items():
                if m[3].endswith(ending):
                    labels[m[1]] = kind
            for ending, part in self.parts.items():
                if m[3].endswith(ending):
                    parts[m[1]] = part
        answer = _dumps({"labels": labels, "summaries": {}, "parts": parts})
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}


def _dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def classifier(provider: Provider, roster: tuple[str, ...] = ()) -> LlmClassifier:
    c = LlmClassifier(api_key="k", model="gemini-test", base_url="http://llm.invalid")
    c._client = provider  # type: ignore[assignment]
    c.use_roster(roster)
    return c


def classify(
    texts: list[str],
    labels: dict[str, str],
    parts: dict[str, str],
    roster: tuple[str, ...] = (),
) -> tuple[list[Prediction], Provider]:
    provider = Provider(labels, parts)
    return classifier(provider, roster).classify(texts), provider


def test_the_request_asks_for_the_part_and_still_fits_the_guard() -> None:
    _, provider = classify([PROMISE] * 40, {}, {})

    assert '"parts"' in llm_module.INSTRUCTIONS
    assert len(llm_module.INSTRUCTIONS) + max(len(t) for t in provider.texts) < 4000


def test_a_short_utterance_comes_back_with_the_words_that_carry_it() -> None:
    with capture_logs() as logs:
        (chat, promise), _ = classify(
            [CHAT, SHORT],
            {"보내겠습니다": "commitment"},
            {"보내겠습니다": PROMISE, "좋네요": "오늘 날씨"},
        )

    assert promise.part == PROMISE
    # A line with no label that takes a part has none, whatever came back.
    assert chat.kind is None and chat.part == ""
    (entry,) = [e for e in logs if e["event"] == "extraction_llm_classified"]
    assert entry["narrowed"] == 1
    # Counts only: the lines are utterances.
    assert "수정본" not in repr(logs)


@pytest.mark.parametrize(
    "returned",
    [
        "수정본을 금요일까지 보내기로 함",  # a summary, not the words
        "수정본은 제가 금요일까지 보내겠습니다!",  # one character more
        "수정본은 제가 목요일까지 보내겠습니다",  # one word changed
        CHAT,  # another line's words
        "",
    ],
)
def test_words_that_are_not_in_the_line_are_never_a_part(returned: str) -> None:
    (_, promise), _ = classify(
        [CHAT, SHORT], {"보내겠습니다": "commitment"}, {"보내겠습니다": returned}
    )

    assert promise.kind is K.COMMITMENT
    assert promise.part == ""


def test_the_part_is_the_lines_own_characters_not_the_answers() -> None:
    """Spacing and quotation marks in the answer are the model's; what is kept
    is cut from the line."""
    (_, promise), _ = classify(
        [CHAT, SHORT],
        {"보내겠습니다": "commitment"},
        {"보내겠습니다": ' "수정본은  제가 금요일까지보내겠습니다" '},
    )

    assert promise.part == PROMISE


def test_a_part_that_is_the_whole_line_is_no_part() -> None:
    (_, promise), _ = classify(
        [CHAT, SHORT], {"보내겠습니다": "commitment"}, {"보내겠습니다": SHORT}
    )

    assert promise.part == ""


def test_a_name_in_the_part_is_cut_from_the_line_as_said_never_the_placeholder() -> None:
    said = f"{REASON} 그 수정본은 김민경 님이 금요일까지 보내기로 했습니다"
    roster = ("김민경",)

    (decided,), provider = classify(
        [said],
        {"했습니다": "decision"},
        {"했습니다": "[사람1] 님이 금요일까지 보내기로 했습니다"},
        roster,
    )

    assert "김민경" not in provider.texts[0] and "[사람1]" in provider.texts[0]
    assert decided.part == "김민경 님이 금요일까지 보내기로 했습니다"
    assert "[사람" not in decided.part


def test_a_placeholder_nobody_sent_is_not_a_part() -> None:
    """With no roster no name was replaced, so a placeholder in the answer is
    the model's own and stands for nothing in the line."""
    line = "그래서 김 대리 님이 보내기로 했습니다 아마 금요일쯤"
    assert usable_part("[사람3] 님이 보내기로 했습니다", line, None) == ""
    assert (
        usable_part("김 대리 님이 보내기로 했습니다", line, None)
        == "김 대리 님이 보내기로 했습니다"
    )
    names = _names({"김민경": {"p1"}})
    assert usable_part("[사람3] 님이 보내기로 했습니다", SHORT, names) == ""


def test_each_piece_of_a_long_turn_has_its_own_part() -> None:
    assert len(LONG) > LONG_TURN_CHARS

    (turn,), _ = classify([LONG], {"받겠습니다.": "commitment"}, {"받겠습니다.": WORDS})

    assert turn.part == ""
    kinds = [kind for _, kind in turn.pieces]
    assert kinds.count(K.COMMITMENT) == 1
    at = kinds.index(K.COMMITMENT)
    assert turn.pieces[at][0] == LONG_PROMISE
    assert turn.piece_parts[at] == WORDS
    assert [p for i, p in enumerate(turn.piece_parts) if i != at] == [""] * (len(kinds) - 1)


def test_a_piece_read_as_a_line_carries_the_part_named_for_it() -> None:
    whole = ClassifiedUtterance(
        id="utt_1",
        kind=K.COMMITMENT,
        confidence=0.9,
        text=f"{FILLER} {LONG_PROMISE}",
        pieces=((FILLER, None), (LONG_PROMISE, K.COMMITMENT)),
        piece_parts=("", WORDS),
    )

    first, second = in_pieces([whole])

    assert (first.part, second.part) == ("", WORDS)
    assert second.piece_parts == () and second.source_id == "utt_1"


def test_parse_parts_drops_what_it_cannot_read() -> None:
    assert parse_parts('{"labels": {}, "parts": {"2": "가 나", "x": "다", "3": 4}}') == {2: "가 나"}
    assert parse_parts('{"labels": {}, "parts": ["가"]}') == {}
    assert parse_parts('{"labels": {}}') == {}
    assert parse_parts("not json") == {}


# --- through the task -----------------------------------------------------------------


class Asked:
    """A resolver that answers with the target."""

    model_version = "asked"

    def resolve(self, requests: list) -> list[str]:
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

        monkeypatch.setattr(tasks, "session_scope", scope)
        monkeypatch.setattr(tasks, "get_nli", FakeNli)
        monkeypatch.setattr(tasks, "get_resolver", Asked)
        monkeypatch.setattr(tasks, "publish", lambda event, payload: [])
        yield session


def answering(
    monkeypatch: pytest.MonkeyPatch, labels: dict[str, str], parts: dict[str, str]
) -> None:
    """The cloud classifier itself, with ``Provider`` behind it."""
    monkeypatch.setattr(tasks, "get_classifier", lambda: classifier(Provider(labels, parts)))


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


def test_an_item_from_a_short_utterance_quotes_the_words_and_keeps_only_where_they_are(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under 300 characters nothing is cut, so before this the reader was shown
    all of it. No word is copied: the row holds two offsets."""
    answering(monkeypatch, {"보내겠습니다": "commitment"}, {"보내겠습니다": PROMISE})

    tasks.on_transcript_ready(meeting(wired, CHAT, SHORT))

    (item,) = wired.query(ExtActionItem).all()
    assert [(s.id, s.excerpt, s.text) for s in service.source_utterances(wired, item.id)] == [
        ("utt_2", PROMISE, SHORT)
    ]
    (row,) = wired.query(ExtActionItemSource).all()
    assert SHORT[row.excerpt_start : row.excerpt_end] == PROMISE


def test_a_decision_from_a_short_utterance_quotes_the_words_on_every_screen(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    answering(monkeypatch, {"했습니다": "decision"}, {"했습니다": SETTLED})

    tasks.on_transcript_ready(meeting(wired, CHAT, DECISION))

    (decision,) = wired.query(ExtDecision).all()
    detail = service.read_decision_detail(wired, decision)
    assert [(s.id, s.excerpt, s.text) for s in detail.sources] == [("utt_2", SETTLED, DECISION)]
    (row,) = wired.query(ExtDecisionSource).all()
    assert DECISION[row.excerpt_start : row.excerpt_end] == SETTLED


def test_in_a_long_turn_the_words_are_quoted_and_not_the_sentence_around_them(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    answering(monkeypatch, {"받겠습니다.": "commitment"}, {"받겠습니다.": WORDS})

    tasks.on_transcript_ready(meeting(wired, CHAT, LONG))

    (item,) = wired.query(ExtActionItem).all()
    assert [(s.excerpt, s.text) for s in service.source_utterances(wired, item.id)] == [
        (WORDS, LONG)
    ]


@pytest.mark.parametrize("returned", ["견적을 수요일까지 받기로 함", FILLER, ""])
def test_in_a_long_turn_words_that_cannot_be_used_leave_the_sentence(
    wired: Session, monkeypatch: pytest.MonkeyPatch, returned: str
) -> None:
    """Reworded, another sentence's, or none: the quotation is the sentence the
    turn was cut into, as it was before the classifier was asked for words."""
    answering(monkeypatch, {"받겠습니다.": "commitment"}, {"받겠습니다.": returned})

    tasks.on_transcript_ready(meeting(wired, CHAT, LONG))

    (item,) = wired.query(ExtActionItem).all()
    assert [s.excerpt for s in service.source_utterances(wired, item.id)] == [LONG_PROMISE]


@pytest.mark.parametrize("returned", ["수정본을 금요일까지 보내기로 함", CHAT, SHORT, ""])
def test_in_a_short_utterance_words_that_cannot_be_used_leave_it_whole(
    wired: Session, monkeypatch: pytest.MonkeyPatch, returned: str
) -> None:
    answering(monkeypatch, {"보내겠습니다": "commitment"}, {"보내겠습니다": returned})

    tasks.on_transcript_ready(meeting(wired, CHAT, SHORT))

    (item,) = wired.query(ExtActionItem).all()
    assert [(s.excerpt, s.text) for s in service.source_utterances(wired, item.id)] == [
        (None, SHORT)
    ]
    (row,) = wired.query(ExtActionItemSource).all()
    assert (row.excerpt_start, row.excerpt_end) == (None, None)


def test_a_rerun_whose_answer_names_no_words_drops_a_decisions_part(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part is this run's: a decision keeps its row across runs, and the
    words named by a run before are not kept under an answer that names none."""
    event = meeting(wired, CHAT, DECISION)
    answering(monkeypatch, {"했습니다": "decision"}, {"했습니다": SETTLED})
    tasks.on_transcript_ready(event)
    (before,) = wired.query(ExtDecision.id).all()

    answering(monkeypatch, {"했습니다": "decision"}, {})
    tasks.on_transcript_ready(event)

    wired.expire_all()
    (decision,) = wired.query(ExtDecision).all()
    assert (decision.id,) == tuple(before)
    (row,) = wired.query(ExtDecisionSource).all()
    assert (row.excerpt_start, row.excerpt_end) == (None, None)


def test_what_is_shown_is_cut_from_the_stored_text_whatever_the_event_carried(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offsets are counted on the stored utterance. Were the stored text to
    differ from the line the words were named in, a part that does not fit it
    is not shown (``cut``) -- never a slice of other words."""
    answering(monkeypatch, {"보내겠습니다": "commitment"}, {"보내겠습니다": PROMISE})
    tasks.on_transcript_ready(meeting(wired, CHAT, SHORT))
    (row,) = wired.query(ExtActionItemSource).all()

    assert cut(SHORT, row.excerpt_start, row.excerpt_end) == PROMISE
    assert cut("짧다", row.excerpt_start, row.excerpt_end) is None
