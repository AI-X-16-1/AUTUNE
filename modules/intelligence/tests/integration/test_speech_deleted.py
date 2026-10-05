"""A person deleted their own speech: E lets go of the words it copied (#587, #614).

E holds B's, C's and D's results as copies and quotes them in meeting reports.
B, C and D forget their own rows on the same signal; E applies the same rules
to its copies -- the work stays, the words go -- and replaces the forgotten text
wherever a report or a correction quotes it.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autune_core import Meeting, Utterance
from autune_intelligence import service
from autune_intelligence.forget import SPEECH_DELETED_TEXT, forget_speech
from autune_intelligence.models import IntelCompletion, IntelMeetingReport

LINE = "결제 API 스펙 초안은 금요일까지 백엔드가 맡기로 했습니다"
"""What the person said and is deleting."""
OTHER = "디자인 시안은 다음 주에 다시 봅시다"
"""What somebody else said, which stays."""
SUMMARY = "결제 API 명세 정리"
"""A model's summary of the line: the team's record, as B keeps it."""


CONFIRMED = {"status": "todo"}
"""A confirmed item: it keeps its entry. A draft awaiting confirmation does not."""


def _meeting(db_session: Session, team: str, day: int = 2) -> str:
    row = Meeting(
        team_id=team, title="결제 회의", started_at=datetime(2026, 10, day, 5, tzinfo=UTC)
    )
    db_session.add(row)
    db_session.flush()
    return row.id


def _utterance(db_session: Session, meeting: str, text: str, at: float) -> str:
    row = Utterance(
        meeting_id=meeting, speaker_label="화자0", start_sec=at, end_sec=at + 3.0, text=text
    )
    db_session.add(row)
    db_session.flush()
    return row.id


def _copies(
    db_session: Session, meeting: str, *, mine: str, theirs: str, previous: str | None = None
) -> IntelCompletion:
    extraction: dict[str, Any] = {
        "contract_version": "2.4",
        "meeting_id": meeting,
        "action_items": [
            {"id": "act_line", "description": LINE, "source_utterance_ids": [mine], **CONFIRMED},
            {
                "id": "act_summary",
                "description": SUMMARY,
                "source_utterance_ids": [mine],
                **CONFIRMED,
            },
            {
                "id": "act_other",
                "description": OTHER,
                "source_utterance_ids": [theirs],
                **CONFIRMED,
            },
        ],
        "decisions": [
            {"id": "dec_line", "statement": LINE, "source_utterance_ids": [mine]},
            {"id": "dec_other", "statement": OTHER, "source_utterance_ids": [theirs]},
        ],
        "classifications": [
            {"utterance_id": mine, "kind": "decision"},
            {"utterance_id": theirs, "kind": "statement"},
        ],
        "ambiguous_agreements": [{"utterance_id": mine, "reason": "확인 필요"}],
    }
    gap = {
        "contract_version": "2.4",
        "meeting_id": meeting,
        "topics": [
            {"id": "topic_mine", "label": "결제 API 스펙", "utterance_ids": [mine]},
            {"id": "topic_both", "label": "디자인 시안", "utterance_ids": [mine, theirs]},
        ],
        "participation": [
            {"topic_id": "topic_mine", "spoke": ["p1"], "silent": []},
            {"topic_id": "topic_both", "spoke": ["p1", "p2"], "silent": []},
        ],
        "gaps": [
            {
                "id": "gap_one",
                "category": "risk",
                "title": "리스크 미논의",
                "related_topic_ids": ["topic_mine", "topic_both"],
                "suggested_question": "결제 API 스펙의 위험은 무엇인가요?",
            },
            {
                "id": "gap_two",
                "category": "owner",
                "title": "담당자 미정",
                "related_topic_ids": ["topic_both"],
                "suggested_question": "디자인 시안은 누가 맡나요?",
            },
        ],
    }
    lineage: list[dict[str, Any]] = [
        {"thread_id": "thr_1", "source_decision_id": "dec_line", "current_statement": LINE}
    ]
    if previous is not None:
        lineage.append(
            {
                "thread_id": "thr_0",
                "source_decision_id": "dec_later",
                "current_statement": "금요일에서 월요일로 미룹니다",
                "previous_statement": LINE,
                "previous_meeting_id": previous,
            }
        )
    context = {
        "contract_version": "2.4",
        "meeting_id": meeting,
        "topic_links": [
            {"topic_label": "결제 API 스펙", "linked_meeting_id": meeting},
            {"topic_label": "디자인 시안", "linked_meeting_id": meeting},
        ],
        "decision_lineage": lineage,
    }
    row = IntelCompletion(
        meeting_id=meeting,
        first_seen_at=datetime.now(UTC),
        extraction_payload=extraction,
        gap_payload=gap,
        context_payload=context,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _report(db_session: Session, meeting: str) -> IntelMeetingReport:
    row = db_session.get(Meeting, meeting)
    assert row is not None
    body = f"✅ 확정된 액션 아이템\n• {LINE} — 백엔드 · 10/3\n• {SUMMARY}\n• {OTHER}"
    service.save_meeting_report(
        db_session, meeting, service.meeting_report_document(row, body), draft_id="rdr_a"
    )
    report = db_session.get(IntelMeetingReport, meeting)
    assert report is not None
    return report


@pytest.fixture
def seeded(db_session: Session, team: str) -> dict[str, str]:
    meeting = _meeting(db_session, team)
    mine = _utterance(db_session, meeting, LINE, 1.0)
    theirs = _utterance(db_session, meeting, OTHER, 5.0)
    _copies(db_session, meeting, mine=mine, theirs=theirs)
    later = _meeting(db_session, team, day=9)
    later_mine = _utterance(db_session, later, "다음 회의 다른 말입니다 그냥 길게", 1.0)
    later_theirs = _utterance(db_session, later, OTHER, 5.0)
    _copies(db_session, later, mine=later_mine, theirs=later_theirs, previous=meeting)
    return {"meeting": meeting, "mine": mine, "theirs": theirs, "later": later}


def test_bs_copy_keeps_the_work_and_drops_the_line(
    db_session: Session, seeded: dict[str, str]
) -> None:
    forget_speech(db_session, [seeded["mine"]])

    row = db_session.get(IntelCompletion, seeded["meeting"])
    assert row is not None and row.extraction_payload is not None
    items = {i["id"]: i["description"] for i in row.extraction_payload["action_items"]}
    decisions = {d["id"]: d["statement"] for d in row.extraction_payload["decisions"]}
    assert items == {"act_line": SPEECH_DELETED_TEXT, "act_summary": SUMMARY, "act_other": OTHER}
    assert decisions == {"dec_line": SPEECH_DELETED_TEXT, "dec_other": OTHER}
    assert [c["utterance_id"] for c in row.extraction_payload["classifications"]] == [
        seeded["theirs"]
    ]
    assert row.extraction_payload["ambiguous_agreements"] == []
    assert LINE not in str(row.extraction_payload)


def test_cs_copy_loses_a_topic_only_the_person_named_and_its_question(
    db_session: Session, seeded: dict[str, str]
) -> None:
    """Including a meeting C does not republish because no topic is left (#614)."""
    forget_speech(db_session, [seeded["mine"]])

    row = db_session.get(IntelCompletion, seeded["meeting"])
    assert row is not None and row.gap_payload is not None
    topics = {t["id"]: t for t in row.gap_payload["topics"]}
    assert set(topics) == {"topic_both"}  # somebody else also named it: it stays
    assert topics["topic_both"]["utterance_ids"] == [seeded["theirs"]]
    assert [p["topic_id"] for p in row.gap_payload["participation"]] == ["topic_both"]
    gaps = {g["id"]: g for g in row.gap_payload["gaps"]}
    assert gaps["gap_one"]["suggested_question"] is None  # named the topic that went
    assert gaps["gap_one"]["related_topic_ids"] == ["topic_both"]
    assert gaps["gap_one"]["title"] == "리스크 미논의"  # the team's finding stays
    assert gaps["gap_two"]["suggested_question"] == "디자인 시안은 누가 맡나요?"


def test_ds_copy_follows_the_decision_here_and_in_later_meetings(
    db_session: Session, seeded: dict[str, str]
) -> None:
    forget_speech(db_session, [seeded["mine"]])

    here = db_session.get(IntelCompletion, seeded["meeting"])
    later = db_session.get(IntelCompletion, seeded["later"])
    assert here is not None and here.context_payload is not None
    assert later is not None and later.context_payload is not None
    assert here.context_payload["decision_lineage"][0]["current_statement"] == SPEECH_DELETED_TEXT
    assert [link["topic_label"] for link in here.context_payload["topic_links"]] == ["디자인 시안"]
    quoted = [c for c in later.context_payload["decision_lineage"] if c["thread_id"] == "thr_0"]
    assert quoted[0]["previous_statement"] == SPEECH_DELETED_TEXT
    assert quoted[0]["current_statement"] == "금요일에서 월요일로 미룹니다"


def test_a_report_and_a_correction_stop_quoting_the_line(
    db_session: Session, seeded: dict[str, str]
) -> None:
    report = _report(db_session, seeded["meeting"])
    report.correction_body = f"정정: {LINE}"
    db_session.flush()

    done = forget_speech(db_session, [seeded["mine"]])

    db_session.refresh(report)
    assert LINE not in report.body_markdown and SPEECH_DELETED_TEXT in report.body_markdown
    assert SUMMARY in report.body_markdown and OTHER in report.body_markdown
    assert report.correction_body == f"정정: {SPEECH_DELETED_TEXT}"
    assert done.reports_changed == 1


def test_forgetting_twice_finds_nothing_the_second_time(
    db_session: Session, seeded: dict[str, str]
) -> None:
    _report(db_session, seeded["meeting"])
    first = forget_speech(db_session, [seeded["mine"]])
    second = forget_speech(db_session, [seeded["mine"]])

    assert first.texts_replaced > 0 and first.reports_changed == 1
    assert (second.texts_replaced, second.topics_removed, second.reports_changed) == (0, 0, 0)


def test_no_utterances_is_a_no_op(db_session: Session) -> None:
    assert forget_speech(db_session, []).meetings == ()


def test_the_hook_forgets_in_its_own_transaction(
    db_session: Session, seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(service, "session_scope", scope)

    service.forget_deleted_speech("user_x", [seeded["mine"]])

    row = db_session.get(IntelCompletion, seeded["meeting"])
    assert row is not None and LINE not in str(row.extraction_payload)


def test_the_hook_is_registered_where_a_router_is_imported() -> None:
    """In a fresh interpreter, as the API process starts: it imports routers and
    never ``tasks``. This file has already imported ``service``."""
    probe = (
        "import autune_intelligence.router\n"
        "from autune_core.deletion import registered_speech_modules\n"
        "assert 'intelligence' in registered_speech_modules()\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- the cases #725's review found -----------------------------------------------------


def test_what_b_deletes_or_rewrites_goes_from_es_copy_too(db_session: Session, team: str) -> None:
    """A draft nobody confirmed goes whatever it says; a short line is caught with
    B's " (담당 ..., 기한 ...)" tail; a decision every line of which is deleted is
    replaced even when tidied; a short item at the head of a report line goes."""
    meeting = _meeting(db_session, team)
    long_line = _utterance(db_session, meeting, LINE, 1.0)
    short_line = _utterance(db_session, meeting, "네 그렇게 하죠", 4.0)
    deploy = _utterance(db_session, meeting, "배포하기", 7.0)
    theirs = _utterance(db_session, meeting, OTHER, 10.0)
    tailed = "네 그렇게 하죠 (담당 민구, 기한 2026-10-09)"
    db_session.add(
        IntelCompletion(
            meeting_id=meeting,
            first_seen_at=datetime.now(UTC),
            extraction_payload={
                "contract_version": "2.4",
                "meeting_id": meeting,
                "action_items": [
                    {
                        "id": "act_draft",
                        "description": "결제 명세 초안 작성",
                        "source_utterance_ids": [long_line],
                        "status": "needs_confirmation",
                    },
                    {
                        "id": "act_deploy",
                        "description": "배포하기",
                        "source_utterance_ids": [deploy],
                        **CONFIRMED,
                    },
                ],
                "decisions": [
                    {
                        # Somebody else's line is a source too: only the tail rule finds it.
                        "id": "dec_short",
                        "statement": tailed,
                        "source_utterance_ids": [short_line, theirs],
                    },
                    {
                        "id": "dec_tidy",
                        "statement": "API 일정은 금요일로 확정",
                        "source_utterance_ids": [long_line],
                    },
                    {
                        "id": "dec_mixed",
                        "statement": "디자인은 다음 주 재검토",
                        "source_utterance_ids": [long_line, theirs],
                    },
                ],
            },
        )
    )
    db_session.flush()
    row = db_session.get(Meeting, meeting)
    assert row is not None
    body = f"• 배포하기 — 김민경 · 10/5\n• {tailed}\n• 디자인은 다음 주 재검토\n• 배포하기 자동화"
    service.save_meeting_report(
        db_session, meeting, service.meeting_report_document(row, body), draft_id="rdr_a"
    )

    forget_speech(db_session, [long_line, short_line, deploy])

    copy = db_session.get(IntelCompletion, meeting)
    assert copy is not None and copy.extraction_payload is not None
    items = {i["id"]: i["description"] for i in copy.extraction_payload["action_items"]}
    decisions = {d["id"]: d["statement"] for d in copy.extraction_payload["decisions"]}
    assert items == {"act_deploy": SPEECH_DELETED_TEXT}  # the draft is gone
    assert decisions == {
        "dec_short": SPEECH_DELETED_TEXT,
        "dec_tidy": SPEECH_DELETED_TEXT,
        "dec_mixed": "디자인은 다음 주 재검토",  # somebody else's line too, not the line
    }
    report = db_session.get(IntelMeetingReport, meeting)
    assert report is not None
    assert "네 그렇게 하죠" not in report.body_markdown
    assert f"• {SPEECH_DELETED_TEXT} — 김민경 · 10/5" in report.body_markdown
    assert "• 배포하기 자동화" in report.body_markdown  # another item that only starts so
    assert "디자인은 다음 주 재검토" in report.body_markdown


def test_a_topic_gone_from_one_meeting_keeps_its_links_in_another(
    db_session: Session, team: str
) -> None:
    """Two meetings forgotten together, each losing the topic the other keeps
    under the same label: each drops only its own topic's links (#725 review)."""
    said: dict[str, tuple[str, str]] = {}
    for day, only_mine, shared in (
        (2, "결제 API 스펙", "디자인 시안"),
        (9, "디자인 시안", "결제 API 스펙"),
    ):
        meeting = _meeting(db_session, team, day=day)
        mine = _utterance(db_session, meeting, f"{only_mine} 이야기를 길게 했습니다", 1.0)
        theirs = _utterance(db_session, meeting, OTHER, 5.0)
        said[meeting] = (mine, shared)
        db_session.add(
            IntelCompletion(
                meeting_id=meeting,
                first_seen_at=datetime.now(UTC),
                gap_payload={
                    "contract_version": "2.4",
                    "meeting_id": meeting,
                    "topics": [
                        {"id": "topic_mine", "label": only_mine, "utterance_ids": [mine]},
                        {"id": "topic_both", "label": shared, "utterance_ids": [mine, theirs]},
                    ],
                    "participation": [],
                    "gaps": [],
                },
                context_payload={
                    "contract_version": "2.4",
                    "meeting_id": meeting,
                    "topic_links": [
                        {"topic_label": only_mine, "linked_meeting_id": meeting},
                        {"topic_label": shared, "linked_meeting_id": meeting},
                    ],
                    "decision_lineage": [],
                },
            )
        )
    db_session.flush()

    forget_speech(db_session, [mine for mine, _ in said.values()])

    for meeting, (_, shared) in said.items():
        row = db_session.get(IntelCompletion, meeting)
        assert row is not None and row.context_payload is not None
        assert [link["topic_label"] for link in row.context_payload["topic_links"]] == [shared]
