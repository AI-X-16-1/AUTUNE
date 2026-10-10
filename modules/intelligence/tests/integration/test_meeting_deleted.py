"""A meeting that is deleted is taken out of E's rows of other meetings (#1161).

Driven the way every caller deletes a meeting -- module A's retention sweep,
its team deletion and a member's own deletion: ``run_meeting_hooks`` with the
real registry, then the ``meetings`` row in a transaction of its own, and the
cascade. What is asserted is what PostgreSQL holds afterwards.

The hook opens its own session, so these commit real rows (the other files
here roll back) and clean up by deleting the teams.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from autune_contracts import CONTRACT_VERSION, ContextLinks
from autune_core import Base, Meeting, Team, get_settings, session_scope
from autune_core.deletion import registered_modules, run_meeting_hooks
from autune_intelligence import service
from autune_intelligence.forget import MeetingForgotten, forget_meeting
from autune_intelligence.models import IntelCompletion, IntelMeetingReport

MARK = "지울회의표식"
"""In every sentence the meeting under deletion wrote, and in nothing any other
meeting wrote."""

TITLE = "결제 회의"
"""The deleted meeting's title. Another meeting of the team and one of another
team carry it too: a weekly meeting's title repeats."""

LINK_PREFIX = "🔗 이어지는 회의: "
"""How the Report template opens its line naming another meeting. Nothing
writes that line yet (D passes no title); the reports seeded here hold it so
that a search by the title would have something to find."""

LINK = f"{LINK_PREFIX}{TITLE}"


@pytest.fixture
def teams(db_engine: object) -> Iterator[tuple[str, str]]:  # db_engine ensures migrations ran
    with session_scope() as s:
        mine = Team(name="intelligence-meeting-deleted-test")
        other = Team(name="intelligence-meeting-deleted-other")
        s.add_all([mine, other])
        s.flush()
        ids = (mine.id, other.id)
    yield ids
    with session_scope() as s:
        s.execute(sa.delete(Team).where(Team.id.in_(ids)))


def _meeting(s, team_id: str, day: int, title: str = TITLE) -> str:  # noqa: ANN001
    # 16:00 UTC the day before is 01:00 in Korea: the team's calendar day is
    # ``day``, and UTC's is not.
    started = datetime(2026, 10, day, 1, tzinfo=timezone(timedelta(hours=9)))
    row = Meeting(team_id=team_id, title=title, started_at=started.astimezone(UTC))
    s.add(row)
    s.flush()
    return row.id


def _change(
    thread: str,
    statement: str,
    *,
    previous: str | None = None,
    previous_meeting: str | None = None,
) -> dict[str, Any]:
    return {
        "thread_id": f"thr_{thread}",
        "source_decision_id": f"dec_{thread}",
        "current_statement": statement,
        "previous_statement": previous,
        "previous_meeting_id": previous_meeting,
        "change_type": "reversed" if previous_meeting else "new",
        "nli_label": "contradiction" if previous_meeting else None,
        "confidence": 0.77 if previous_meeting else 0.9,
        "key_stakeholders_absent": [],
    }


def _completion(  # noqa: ANN001
    s,
    meeting_id: str,
    lineage: list[dict[str, Any]],
    links: list[dict[str, Any]] | None = None,
    extraction: dict[str, Any] | None = None,
) -> None:
    s.add(
        IntelCompletion(
            meeting_id=meeting_id,
            first_seen_at=datetime.now(UTC),
            extraction_payload=extraction,
            context_payload={
                "contract_version": CONTRACT_VERSION,
                "meeting_id": meeting_id,
                "topic_links": links or [],
                "decision_lineage": lineage,
                "missing_sources": [],
            },
        )
    )
    s.flush()


def _report(s, meeting_id: str, body: str, correction: str | None = None) -> None:  # noqa: ANN001
    meeting = s.get(Meeting, meeting_id)
    assert meeting is not None
    row = service.save_meeting_report(
        s, meeting_id, service.meeting_report_document(meeting, body), draft_id="rdr_a"
    )
    row.correction_body = correction
    s.flush()


@dataclass(frozen=True)
class Seeded:
    earlier: str  # 10/1, another title
    meeting: str  # 10/2 -- the one deleted
    later: str  # 10/3, another title
    same_title: str  # 10/9, the deleted meeting's title
    elsewhere: str  # another team's, 10/2, the same title


def seed(teams: tuple[str, str]) -> Seeded:
    team_id, other_team = teams
    with session_scope() as s:
        earlier = _meeting(s, team_id, 1, "기획 회의")
        meeting = _meeting(s, team_id, 2)
        later = _meeting(s, team_id, 3, "출시 회의")
        same_title = _meeting(s, team_id, 9)
        elsewhere = _meeting(s, other_team, 2)

        _completion(s, earlier, [_change("a", "회식은 화요일에 한다")])
        # Its own rows: a copy of its statement following its own, B's result.
        _completion(
            s,
            meeting,
            [
                _change("b", f"{MARK} 배포는 금요일에 한다"),
                _change(
                    "c",
                    f"{MARK} 가격은 다시 올린다",
                    previous=f"{MARK} 가격은 내린다",
                    previous_meeting=meeting,
                ),
            ],
            extraction={
                "decisions": [{"id": "dec_b", "statement": f"{MARK} 배포는 금요일에 한다"}]
            },
        )
        _completion(
            s,
            later,
            [
                _change(
                    "b",
                    "배포는 월요일로 미룬다",
                    previous=f"{MARK} 배포는 금요일에 한다",
                    previous_meeting=meeting,
                ),
                _change(
                    "a",
                    "회식은 목요일에 한다",
                    previous="회식은 화요일에 한다",
                    previous_meeting=earlier,
                ),
                _change("d", "새 결정"),
            ],
            links=[
                {
                    "topic_label": "배포 일정",
                    "linked_meeting_id": meeting,
                    "linked_meeting_date": "2026-10-02",
                    "similarity": 0.9,
                    "rerank_score": 0.9,
                }
            ],
        )
        _completion(
            s,
            same_title,
            [
                _change(
                    "c",
                    "가격은 그대로 둔다",
                    previous=f"{MARK} 가격은 다시 올린다",
                    previous_meeting=meeting,
                )
            ],
        )
        _completion(
            s,
            elsewhere,
            [
                _change(
                    "x",
                    "다른 팀의 결정을 바꾼다",
                    previous="다른 팀의 결정",
                    previous_meeting=elsewhere,
                )
            ],
        )

        # Its own report, naming itself: left whole, like its own copy.
        _report(s, meeting, f"✅ 확정된 할 일\n• {MARK} 배포 준비\n{LINK} (10/2)")
        _report(
            s,
            later,
            "\n".join(
                [
                    "✅ 확정된 할 일",
                    "• 배포 공지 쓰기 — 민수 · 10/5",
                    f"{LINK} (10/2)",
                    f"{LINK_PREFIX}기획 회의 (10/1)",
                ]
            ),
            correction=f"정정: 공지는 화요일에 냅니다\n{LINK} (2026-10-02)",
        )
        _report(
            s,
            earlier,
            "\n".join(
                [
                    f"{LINK}",  # no date: the title alone names it
                    f"{LINK} (10/9)",  # the other meeting of that title
                    f"{LINK} 준비 (10/2)",  # another title that starts the same
                    f"{TITLE} (10/2)에서 정한 대로 진행합니다",  # a person's sentence
                ]
            ),
        )
        _report(s, elsewhere, f"{LINK} (10/2)")
        return Seeded(
            earlier=earlier,
            meeting=meeting,
            later=later,
            same_title=same_title,
            elsewhere=elsewhere,
        )


def delete_as_a_caller_does(meeting_id: str) -> None:
    """Hooks first, each in its own transaction; then the row, and the cascade."""
    run_meeting_hooks(meeting_id)
    with session_scope() as s:
        s.execute(sa.delete(Meeting).where(Meeting.id == meeting_id))


def lineage(meeting_id: str) -> dict[str, dict[str, Any]]:
    """The meeting's copy of D's lineage, by thread -- validated as the contract."""
    with session_scope() as s:
        row = s.get(IntelCompletion, meeting_id)
        assert row is not None and row.context_payload is not None
        ContextLinks.model_validate(row.context_payload)
        return {c["thread_id"]: c for c in row.context_payload["decision_lineage"]}


def report(meeting_id: str) -> tuple[list[str], str | None]:
    """The report's body lines between E's header and footer, and its correction."""
    with session_scope() as s:
        row = s.get(IntelMeetingReport, meeting_id)
        assert row is not None
        return row.body_markdown.split("\n\n")[1].split("\n"), row.correction_body


def rows_holding(text: str) -> dict[str, int]:
    """Every ``intel_`` table with a row whose any column reads ``text``, counted.

    Reads each row as PostgreSQL prints it, so a column added later is covered
    without being named here."""
    found: dict[str, int] = {}
    with session_scope() as s:
        for table in Base.metadata.sorted_tables:
            if not table.name.startswith("intel_"):
                continue
            count = s.scalar(
                sa.text(f"SELECT count(*) FROM {table.name} AS t WHERE t::text LIKE :needle"),  # noqa: S608
                {"needle": f"%{text}%"},
            )
            if count:
                found[table.name] = count
    return found


def snapshot() -> list[tuple]:
    """Every copy and report of the seeded teams' meetings, as stored."""
    with session_scope() as s:
        copies = s.execute(
            sa.select(
                IntelCompletion.meeting_id,
                sa.cast(IntelCompletion.context_payload, sa.Text),
                sa.cast(IntelCompletion.extraction_payload, sa.Text),
            ).order_by(IntelCompletion.meeting_id)
        ).all()
        reports = s.execute(
            sa.select(
                IntelMeetingReport.meeting_id,
                IntelMeetingReport.body_markdown,
                IntelMeetingReport.correction_body,
                IntelMeetingReport.draft_id,
            ).order_by(IntelMeetingReport.meeting_id)
        ).all()
    return [tuple(row) for row in copies] + [tuple(row) for row in reports]


# --------------------------------------------------------------------------- #
# What is left once the meeting has gone
# --------------------------------------------------------------------------- #


def test_nothing_the_meeting_wrote_is_left_in_any_intel_table(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    assert rows_holding(MARK) == {"intel_completion": 3, "intel_meeting_reports": 1}

    delete_as_a_caller_does(seeded.meeting)

    assert rows_holding(MARK) == {}


def test_the_cascade_alone_leaves_the_statement_in_later_meetings(teams: tuple[str, str]) -> None:
    """What the hook is for: without it two other meetings still quote it."""
    seeded = seed(teams)

    with session_scope() as s:
        s.execute(sa.delete(Meeting).where(Meeting.id == seeded.meeting))

    assert rows_holding(MARK) == {"intel_completion": 2}


def test_a_later_meetings_copy_is_emptied_and_what_changed_stays(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    later = lineage(seeded.later)
    assert later["thr_b"] == _change(
        "b", "배포는 월요일로 미룬다", previous=None, previous_meeting=seeded.meeting
    )
    # A change that followed another meeting, and one that followed none.
    assert later["thr_a"]["previous_statement"] == "회식은 화요일에 한다"
    assert later["thr_a"]["previous_meeting_id"] == seeded.earlier
    assert later["thr_d"] == _change("d", "새 결정")
    assert lineage(seeded.same_title)["thr_c"]["previous_statement"] is None
    assert lineage(seeded.same_title)["thr_c"]["current_statement"] == "가격은 그대로 둔다"


def test_the_rest_of_a_later_meetings_copy_is_as_it_was(teams: tuple[str, str]) -> None:
    """The topic link to the deleted meeting holds its id and date and none of
    its words; it is not this hook's to drop."""
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    with session_scope() as s:
        row = s.get(IntelCompletion, seeded.later)
        assert row is not None and row.context_payload is not None
        assert row.context_payload["topic_links"] == [
            {
                "topic_label": "배포 일정",
                "linked_meeting_id": seeded.meeting,
                "linked_meeting_date": "2026-10-02",
                "similarity": 0.9,
                "rerank_score": 0.9,
            }
        ]
        assert row.context_payload["meeting_id"] == seeded.later
        assert row.context_payload["contract_version"] == CONTRACT_VERSION


def test_another_teams_copy_is_left_alone(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    theirs = lineage(seeded.elsewhere)["thr_x"]
    assert theirs["previous_statement"] == "다른 팀의 결정"
    assert theirs["previous_meeting_id"] == seeded.elsewhere


def test_no_report_is_searched_for_the_meeting(teams: tuple[str, str]) -> None:
    """lsh2217 on #1161: only the copies of D's lineage. Every other meeting's
    report and correction is as it was, whatever names the deleted meeting in
    it -- the template's line with its title, with or without its date, the
    same line for another meeting of that title, a sentence a person wrote."""
    seeded = seed(teams)
    others = {seeded.earlier, seeded.later, seeded.same_title, seeded.elsewhere}
    before = [row for row in snapshot() if len(row) == 4 and row[0] in others]
    assert len(before) == 3
    assert sum(TITLE in row[1] or TITLE in (row[2] or "") for row in before) == 3

    delete_as_a_caller_does(seeded.meeting)

    assert [row for row in snapshot() if len(row) == 4] == before


# --------------------------------------------------------------------------- #
# The hook on its own: before the row goes, twice, and when nothing is there
# --------------------------------------------------------------------------- #


def test_the_hook_is_done_before_the_row_goes_and_leaves_the_meetings_own_rows(
    teams: tuple[str, str],
) -> None:
    """The caller deletes in a transaction the hook is not part of, and may
    fail to. The meeting's own copy and report are then as they were."""
    seeded = seed(teams)

    run_meeting_hooks(seeded.meeting)

    with session_scope() as s:
        assert s.get(Meeting, seeded.meeting) is not None
    assert lineage(seeded.later)["thr_b"]["previous_statement"] is None
    own = lineage(seeded.meeting)
    assert own["thr_c"]["previous_statement"] == f"{MARK} 가격은 내린다"
    assert own["thr_c"]["previous_meeting_id"] == seeded.meeting
    assert own["thr_b"]["current_statement"] == f"{MARK} 배포는 금요일에 한다"
    assert report(seeded.meeting)[0] == [
        "✅ 확정된 할 일",
        f"• {MARK} 배포 준비",
        f"{LINK} (10/2)",
    ]


def test_running_the_hook_again_changes_nothing_more(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    run_meeting_hooks(seeded.meeting)
    once = snapshot()

    with session_scope() as s:
        again = forget_meeting(s, seeded.meeting)

    assert again == MeetingForgotten()
    assert snapshot() == once


def test_running_it_after_the_row_went_changes_nothing(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    delete_as_a_caller_does(seeded.meeting)
    gone = snapshot()

    run_meeting_hooks(seeded.meeting)

    assert snapshot() == gone


def test_a_copy_left_after_the_row_went_is_still_emptied(teams: tuple[str, str]) -> None:
    """The copies are found by the meeting's id alone, so a run that comes
    after the row -- a deletion that happened before this hook existed -- still
    empties them."""
    seeded = seed(teams)
    with session_scope() as s:
        s.execute(sa.delete(Meeting).where(Meeting.id == seeded.meeting))

    with session_scope() as s:
        done = forget_meeting(s, seeded.meeting)

    assert done == MeetingForgotten(statements_cleared=2)
    assert rows_holding(MARK) == {}


def test_a_meeting_e_holds_nothing_of_changes_nothing(teams: tuple[str, str]) -> None:
    seed(teams)
    with session_scope() as s:
        bare = _meeting(s, teams[0], 20, "아무도 모르는 회의")
    before = snapshot()

    with session_scope() as s:
        assert forget_meeting(s, bare) == MeetingForgotten()
        assert forget_meeting(s, "mtg_nobody") == MeetingForgotten()

    assert snapshot() == before


def test_the_counts_say_what_was_done(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    with session_scope() as s:
        done = forget_meeting(s, seeded.meeting)

    # Two later meetings' copies.
    assert done == MeetingForgotten(statements_cleared=2)


def test_the_log_line_carries_ids_and_counts_and_no_text(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    with capture_logs() as logs:
        run_meeting_hooks(seeded.meeting)

    mine = [entry for entry in logs if entry["event"] == "intelligence_meeting_forgotten"]
    assert mine == [
        {
            "event": "intelligence_meeting_forgotten",
            "log_level": "info",
            "meeting_id": seeded.meeting,
            "statements_cleared": 2,
        }
    ]
    assert MARK not in str(logs)
    assert TITLE not in str(logs)


# --------------------------------------------------------------------------- #
# As a hook: registered, raising, and under the caller's lock
# --------------------------------------------------------------------------- #


def test_a_failure_reaches_the_caller_and_nothing_is_half_done(
    teams: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller keeps the meeting when a hook raises. One transaction: a
    failure after the copies were emptied leaves them as they were."""
    seeded = seed(teams)
    before = snapshot()

    def fails(*_args: object, **_kwargs: object) -> MeetingForgotten:
        raise RuntimeError("no")

    # The last thing the function does, after the copies were emptied and flushed.
    monkeypatch.setattr("autune_intelligence.forget.MeetingForgotten", fails)
    with pytest.raises(RuntimeError):
        run_meeting_hooks(seeded.meeting)
    monkeypatch.undo()

    assert snapshot() == before


def test_the_hook_does_not_wait_for_the_callers_lock(teams: tuple[str, str]) -> None:
    """A team's deletion holds ``FOR NO KEY UPDATE`` on every meeting of the
    team, and a member's on the one meeting, while the hooks run in sessions
    of their own. A hook that needed one of those rows would wait for its own
    caller."""
    seeded = seed(teams)
    engine = sa.create_engine(get_settings().database_url)
    try:
        with engine.connect() as caller:
            caller.execute(
                sa.select(Meeting.id)
                .where(Meeting.team_id == teams[0])
                .with_for_update(key_share=True)
            )
            caller.execute(
                sa.select(Team.id).where(Team.id == teams[0]).with_for_update(key_share=True)
            )
            failed: list[BaseException] = []

            def run() -> None:
                try:
                    run_meeting_hooks(seeded.meeting)
                except BaseException as exc:  # noqa: BLE001 -- reported below
                    failed.append(exc)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            worker.join(timeout=20)
            waiting = worker.is_alive()
            caller.rollback()
            worker.join(timeout=20)
    finally:
        engine.dispose()

    assert not waiting
    assert failed == []
    assert lineage(seeded.later)["thr_b"]["previous_statement"] is None


def test_the_hook_is_registered_in_this_process() -> None:
    assert "intelligence" in registered_modules()[0]


@pytest.mark.parametrize("entry", ["autune_intelligence.router", "autune_intelligence.tasks"])
def test_the_hook_is_registered_where_the_api_and_the_worker_start(entry: str) -> None:
    """In a fresh interpreter. The API process, where a member or a team's
    last member deletes, imports routers and never ``tasks``; the worker,
    where a meeting expires, imports ``tasks``. This file has already imported
    ``service``, so an in-process check would pass without either."""
    probe = (
        f"import {entry}\n"
        "from autune_core.deletion import registered_modules\n"
        "assert 'intelligence' in registered_modules()[0]\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
