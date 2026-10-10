"""One sentence that holds two things to do is two rows (module B's owner,
2026-10-09): "로그는 제가 14일까지 정리하고, 문구는 16일까지 고칠게요" names two
objects, each with its own date. A turn is cut by rule at sentence ends and
this is inside a sentence, so the classifier's answer says where: the line's
number with ``-1``, ``-2`` after it, and for each the words of the line that
say it.

What is tested here is that the model only ever chooses *where* the line is
cut. Every piece is the line's own characters; an answer that cannot cut the
line -- a part that is not in it, one part, parts that overlap -- still labels
it, as the one line it was. And one object with two verbs is one row: that is
the model's call under the instructions, and an answer that does not cut is
read as before.

No network: a fake provider answers the real classifier's request, and the
task runs for real. Every line is invented.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

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
from autune_extraction.models import ExtActionItem
from autune_extraction.pipeline import FakeNli, Prediction
from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline.llm import (
    LONG_TURN_CHARS,
    LlmClassifier,
    _names,
    cut_in_two,
    parse,
    parse_split,
    strongest,
)

K = UtteranceKind
C, D = K.COMMITMENT, K.DECISION
MEETING = "mtg_1"
HELD = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)

CHAT = "오늘 날씨가 참 좋네요"
FIRST = "로그는 제가 14일까지 정리하고"
SECOND = "문구는 16일까지 고칠게요"
TWO = f"{FIRST}, {SECOND}"
"""Two objects, each with its own date, in one sentence."""
ONE_OBJECT = "시안은 제가 금요일까지 고쳐서 공유할게요"
"""One object and two verbs: one thing to do."""

FILLER = "그 부분은 지난 분기에도 같은 이야기가 나왔던 것으로 기억합니다."


# --- reading the answer -----------------------------------------------------------------


def test_parse_split_reads_the_pieces_of_a_line_in_the_order_of_their_numbers() -> None:
    answer = json.dumps(
        {
            "labels": {"8-2": "decision", "8-1": "commitment", "3": "commitment"},
            "summaries": {"8-1": "로그  정리", "8-2": "문구 수정"},
            "parts": {"8-1": FIRST, "8-2": f" {SECOND} "},
        },
        ensure_ascii=False,
    )

    assert parse_split("앞말 " + answer + " 뒷말") == {
        8: [(C, "로그 정리", FIRST), (D, "문구 수정", SECOND)]
    }


def test_parse_split_drops_what_it_cannot_read() -> None:
    read = parse_split(
        '{"labels": {"8-1": "commitment", "8-2": "vote", "8-x": "decision", "9-1-2": "decision",'
        ' "7": "decision"}, "summaries": ["가"], "parts": {"8-1": 4}}'
    )

    # An unknown kind and a key of another shape are dropped; a summary or a
    # part that is not there, or is not text, is "".
    assert read == {8: [(C, "", "")]}
    assert parse_split("not json") == {}
    assert parse_split('{"labels": ["commitment"]}') == {}
    assert parse_split("{]") == {}
    assert parse_split("[1, 2]") == {}


def test_a_piece_key_is_not_a_line_to_the_reader_of_whole_lines() -> None:
    """``parse`` labels whole lines; "8-1" is not one of them, so a cut line is
    labelled by the split's own reading and nothing else."""
    assert parse('{"labels": {"8-1": "commitment", "8-2": "decision"}}') == {}


# --- where the line is cut --------------------------------------------------------------


def test_the_pieces_are_the_lines_own_characters_in_spoken_order() -> None:
    line = f"그러면  {FIRST},   {SECOND} 아마도요"
    answered = [(D, "문구 수정", "문구는 16일까지고칠게요"), (C, "로그 정리", f'"{FIRST}"')]

    assert cut_in_two(answered, line, None) == [(FIRST, C, "로그 정리"), (SECOND, D, "문구 수정")]


def test_words_outside_every_part_belong_to_no_piece() -> None:
    line = f"음 그러니까 {FIRST}, 아 그리고 {SECOND} 네"

    pieces = cut_in_two([(C, "", FIRST), (C, "", SECOND)], line, None)

    assert [words for words, _, _ in pieces] == [FIRST, SECOND]


@pytest.mark.parametrize(
    "answered",
    [
        [],
        [(C, "", FIRST)],  # one piece is not a cut
        [(C, "", FIRST), (C, "", "문구는 17일까지 고칠게요")],  # one word changed
        [(C, "", FIRST), (C, "", "")],  # nothing given for one
        [(C, "", FIRST), (C, "", CHAT)],  # another line's words
        [(C, "", f"{FIRST}, 문구는"), (C, "", SECOND)],  # they overlap
        [(C, "", FIRST), (C, "", FIRST)],  # the same words twice
    ],
)
def test_an_answer_that_cannot_cut_the_line_cuts_nothing(
    answered: list[tuple[UtteranceKind, str, str]],
) -> None:
    assert cut_in_two(answered, TWO, None) == []


def test_a_name_in_a_part_is_cut_from_the_line_as_said() -> None:
    line = "로그는 김민경 님이 14일까지 정리하고, 문구는 16일까지 고치기로 했습니다"
    names = _names({"김민경": {"p1"}})
    answered = [
        (D, "", "로그는 [사람1] 님이 14일까지 정리하고"),
        (D, "", "문구는 16일까지 고치기로 했습니다"),
    ]

    pieces = cut_in_two(answered, line, names)

    assert [words for words, _, _ in pieces] == [
        "로그는 김민경 님이 14일까지 정리하고",
        "문구는 16일까지 고치기로 했습니다",
    ]
    # With no roster no placeholder was sent, so one in the answer stands for nothing.
    assert cut_in_two(answered, line, None) == []


# --- the classifier's side --------------------------------------------------------------

Cut = list[tuple[str, str, str]]
"""How a provider answers a line it cuts: ``(kind, part, summary)`` a piece."""


class Provider:
    """Answers like ``generateContent``. A line that ends with a key of
    ``whole`` is labelled as one line; one that ends with a key of ``cut`` is
    answered as the pieces given -- for context lines too, to prove those are
    not read."""

    def __init__(self, whole: dict[str, str], cut: dict[str, Cut], extra: dict | None = None):
        self.whole = whole
        self.cut = cut
        self.extra = extra or {}
        self.narrowing = False
        self.texts: list[str] = []

    def request(self, method: str, path: str, *, json: dict) -> dict:  # noqa: A002 - httpx's name
        text = json["contents"][0]["parts"][0]["text"]
        self.texts.append(text)
        labels: dict[str, str] = dict(self.extra)
        summaries: dict[str, str] = {}
        parts: dict[str, str] = {}
        for line in text.splitlines():
            m = re.match(r"(\d+) \[(대상|문맥)\] (.*)", line)
            if not m:
                continue
            for ending, kind in self.whole.items():
                if m[3].endswith(ending):
                    labels[m[1]] = kind
                    if self.narrowing:
                        # The line's first words, as the part of it as a whole.
                        parts[m[1]] = m[3].split(",")[0]
            for ending, pieces in self.cut.items():
                if not m[3].endswith(ending):
                    continue
                for number, (kind, part, summary) in enumerate(pieces, start=1):
                    labels[f"{m[1]}-{number}"] = kind
                    parts[f"{m[1]}-{number}"] = part
                    summaries[f"{m[1]}-{number}"] = summary
        answer = _dumps({"labels": labels, "summaries": summaries, "parts": parts})
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
    cut: dict[str, Cut],
    whole: dict[str, str] | None = None,
    roster: tuple[str, ...] = (),
    extra: dict | None = None,
) -> tuple[list[Prediction], Provider]:
    provider = Provider(whole or {}, cut, extra)
    return classifier(provider, roster).classify(texts), provider


BOTH: Cut = [("commitment", FIRST, ""), ("commitment", SECOND, "")]


def test_the_request_says_when_to_cut_a_line_and_when_not_to() -> None:
    assert '"8-1"' in llm_module.INSTRUCTIONS and "-1, -2" in llm_module.INSTRUCTIONS
    assert "나누지 마세요" in llm_module.INSTRUCTIONS
    _, provider = classify([TWO] * 40, {})
    assert len(llm_module.INSTRUCTIONS) + max(len(t) for t in provider.texts) < 4000


def test_a_line_answered_as_two_things_comes_back_as_two_pieces() -> None:
    with capture_logs() as logs:
        (chat, two), _ = classify([CHAT, TWO], {"고칠게요": BOTH})

    assert chat.kind is None and chat.pieces == ()
    assert two.kind is C
    assert two.pieces == ((FIRST, C), (SECOND, C))
    # The line is not narrowed as one thing any more: each piece is its own.
    assert (two.summary, two.part) == ("", "")
    assert two.piece_parts == ("", "")
    (entry,) = [e for e in logs if e["event"] == "extraction_llm_classified"]
    assert entry["split"] == 1
    # Counts only: the lines are utterances.
    assert "로그" not in repr(logs) and "문구" not in repr(logs)


def test_the_pieces_are_in_spoken_order_whatever_order_the_answer_gives() -> None:
    backwards: Cut = [("decision", SECOND, ""), ("commitment", FIRST, "")]

    (two,), _ = classify([TWO], {"고칠게요": backwards})

    assert two.pieces == ((FIRST, C), (SECOND, D))
    assert two.kind is strongest([C, D])


@pytest.mark.parametrize(
    "cut",
    [
        [("commitment", FIRST, ""), ("commitment", "문구를 16일까지 고치기로 함", "")],  # reworded
        [("commitment", FIRST, "")],  # one piece
        [("commitment", f"{FIRST}, 문구는", ""), ("commitment", SECOND, "")],  # overlapping
    ],
)
def test_a_cut_that_cannot_be_used_still_labels_the_line_as_one(cut: Cut) -> None:
    with capture_logs() as logs:
        (two,), _ = classify([TWO], {"고칠게요": cut})

    assert two.kind is C, "the label is not lost to a bad cut"
    assert two.pieces == ()
    (entry,) = [e for e in logs if e["event"] == "extraction_llm_classified"]
    assert entry["split"] == 0


def test_a_bad_cut_of_two_kinds_gives_the_line_the_stronger_one() -> None:
    cut: Cut = [("decision", "로그 정리", ""), ("commitment", SECOND, "")]

    (two,), _ = classify([TWO], {"고칠게요": cut})

    assert two.pieces == ()
    assert two.kind is strongest([D, C])


def test_a_line_also_labelled_whole_keeps_the_stronger_kind_and_is_still_cut() -> None:
    """A model that answers "2" and "2-1", "2-2" both: the cut is used."""
    provider = Provider({"고칠게요": "decision"}, {"고칠게요": BOTH})
    provider.narrowing = True
    with capture_logs() as logs:
        (two,) = classifier(provider).classify([TWO])

    assert two.pieces == ((FIRST, C), (SECOND, C))
    assert two.kind is C
    # What came back for the line as a whole is not used, and not counted as used.
    assert (two.summary, two.part) == ("", "")
    (entry,) = [e for e in logs if e["event"] == "extraction_llm_classified"]
    assert (entry["split"], entry["narrowed"]) == (1, 0)


def test_a_line_number_that_is_not_in_the_request_is_ignored() -> None:
    (chat, promise), _ = classify(
        [CHAT, ONE_OBJECT], {}, whole={"공유할게요": "commitment"}, extra={"99-1": "decision"}
    )

    assert (chat.kind, promise.kind) == (None, C)
    assert promise.pieces == ()


def test_one_object_with_two_verbs_answered_as_one_line_is_one_thing() -> None:
    (promise,), _ = classify([ONE_OBJECT], {}, whole={"공유할게요": "commitment"})

    assert promise.kind is C and promise.pieces == ()


def test_a_pieces_summary_is_checked_against_the_piece_not_the_rest_of_the_line() -> None:
    """The line is cut because each thing has its own date. Checked against the
    whole line, the second piece's summary could carry the first one's."""
    cut: Cut = [
        ("commitment", FIRST, "로그를 14일까지 정리"),
        ("commitment", SECOND, "문구를 14일까지 수정"),  # the other piece's date
    ]

    (two,), _ = classify([TWO], {"고칠게요": cut})

    assert two.piece_summaries == ("로그를 14일까지 정리", "")


def test_a_pieces_summary_may_use_what_was_said_just_before_the_line() -> None:
    asked = "3차 배포 준비는 누가 하실래요?"
    cut: Cut = [
        ("commitment", FIRST, "3차 배포 로그를 14일까지 정리"),
        ("commitment", SECOND, "3차 배포 문구를 16일까지 수정"),
    ]

    (_, two), _ = classify([asked, TWO], {"고칠게요": cut})

    assert two.piece_summaries == ("3차 배포 로그를 14일까지 정리", "3차 배포 문구를 16일까지 수정")


def test_a_name_goes_out_replaced_and_the_pieces_come_back_as_said() -> None:
    line = "로그는 김민경 님이 14일까지 정리하고, 문구는 16일까지 고치기로 했습니다"
    cut: Cut = [
        ("decision", "로그는 [사람1] 님이 14일까지 정리하고", ""),
        ("decision", "문구는 16일까지 고치기로 했습니다", ""),
    ]

    (decided,), provider = classify([line], {"했습니다": cut}, roster=("김민경",))

    assert "김민경" not in provider.texts[0] and "[사람1]" in provider.texts[0]
    assert [words for words, _ in decided.pieces] == [
        "로그는 김민경 님이 14일까지 정리하고",
        "문구는 16일까지 고치기로 했습니다",
    ]


def test_a_sentence_of_a_long_turn_is_cut_among_the_turns_other_sentences() -> None:
    turn = " ".join([FILLER] * 5 + [f"{TWO}."] + [FILLER] * 4)
    assert len(turn) > LONG_TURN_CHARS
    cut: Cut = [("commitment", FIRST, ""), ("commitment", SECOND, "")]

    (read,), _ = classify([turn], {"고칠게요.": cut})

    assert read.kind is C
    assert [words for words, kind in read.pieces if kind is not None] == [FIRST, SECOND]
    assert [words for words, kind in read.pieces if kind is None] == [FILLER] * 9
    assert len(read.piece_summaries) == len(read.piece_parts) == len(read.pieces) == 11


def test_each_piece_is_read_as_a_line_of_its_own_from_here_on() -> None:
    whole = ClassifiedUtterance(
        id="utt_1",
        kind=C,
        confidence=0.9,
        text=TWO,
        pieces=((FIRST, C), (SECOND, C)),
        piece_summaries=("로그를 14일까지 정리", ""),
        piece_parts=("", ""),
    )

    first, second = in_pieces([whole])

    assert (first.id, first.text, first.summary) == ("utt_1#1", FIRST, "로그를 14일까지 정리")
    assert (second.id, second.text, second.summary) == ("utt_1#2", SECOND, "")
    assert first.source_id == second.source_id == "utt_1"


# --- through the task -------------------------------------------------------------------


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
        # Held on a known day: a date said in a line is read against it.
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의", started_at=HELD))
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


def quoted_by(session: Session) -> dict[tuple[str, str | None], ExtActionItem]:
    """The meeting's items, each by the utterance it cites and the words of it
    a reader is shown. The item's own sentence is rewritten on the way (the
    ending, the speaker's "제가"), so what tells the items apart here is what
    each was made from."""
    found = {}
    for item in session.query(ExtActionItem).all():
        (source,) = service.source_utterances(session, item.id)
        found[(source.id, source.excerpt)] = item
    return found


def test_a_sentence_with_two_things_to_do_is_two_items_each_quoting_its_own_words(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tasks, "get_classifier", lambda: classifier(Provider({}, {"고칠게요": BOTH}))
    )

    tasks.on_transcript_ready(meeting(wired, CHAT, TWO))

    items = quoted_by(wired)
    assert set(items) == {("utt_2", FIRST), ("utt_2", SECOND)}
    first, second = items[("utt_2", FIRST)], items[("utt_2", SECOND)]
    # Each is written from its own words and has the date said with it.
    assert "로그" in first.description and "문구" not in first.description
    assert "문구" in second.description and "로그" not in second.description
    assert first.due_date == date(2026, 10, 14)
    assert second.due_date == date(2026, 10, 16)


def test_the_same_sentence_answered_as_one_line_is_one_item_as_before(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tasks, "get_classifier", lambda: classifier(Provider({"고칠게요": "commitment"}, {}))
    )

    tasks.on_transcript_ready(meeting(wired, CHAT, TWO))

    ((cited, item),) = quoted_by(wired).items()
    assert cited == ("utt_2", None), "all of the utterance, so nothing narrower to quote"
    assert "로그" in item.description and "문구" in item.description


def test_a_cut_that_cannot_be_used_leaves_one_item_from_the_whole_line(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cut: Cut = [("commitment", FIRST, ""), ("commitment", "문구를 16일까지 고치기로 함", "")]
    monkeypatch.setattr(
        tasks, "get_classifier", lambda: classifier(Provider({}, {"고칠게요": cut}))
    )

    tasks.on_transcript_ready(meeting(wired, CHAT, TWO))

    ((cited, item),) = quoted_by(wired).items()
    assert cited == ("utt_2", None)
    assert "로그" in item.description and "문구" in item.description
