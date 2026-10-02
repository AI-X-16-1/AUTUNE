"""The dashboard's meeting-report card: list a team's reports, edit a draft (10/2).

A report is meeting text, so unlike E's older aggregate routes these
authenticate and check team membership. A draft may be edited by any member of
the team until it is posted; the editor and the time are recorded. An edit
takes a new ``draft_id``, so an approval given for the model's text can never
post a person's text, and the person who last edited it posts it from the card
(#642 review, decided with the user 10/2). A posted report is not edited here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_intelligence import service, tools
from autune_intelligence.models import IntelMeetingReport
from autune_intelligence.router import router

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _user(db_session: Session, team: str | None, name: str = "이승환") -> User:
    user = User(email=f"{name}-{datetime.now(UTC).timestamp()}@example.com", display_name=name)
    db_session.add(user)
    db_session.flush()
    if team is not None:
        db_session.add(TeamMember(team_id=team, user_id=user.id))
        db_session.flush()
    return user


@pytest.fixture
def client_for(db_session: Session) -> Iterator[Callable[[User], TestClient]]:
    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/intelligence")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    yield build


def _meeting(db_session: Session, team: str, title: str, day: int) -> str:
    row = Meeting(team_id=team, title=title, started_at=datetime(2026, 10, day, 5, 0, tzinfo=UTC))
    db_session.add(row)
    db_session.flush()
    return row.id


def _report(db_session: Session, meeting: str, *, draft_id: str = "rdr_a") -> None:
    row = db_session.get(Meeting, meeting)
    assert row is not None
    document = service.meeting_report_document(row, BODY)
    service.save_meeting_report(db_session, meeting, document, draft_id=draft_id)


# --- list ------------------------------------------------------------------------


def test_a_member_lists_the_teams_reports_newest_meeting_first(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    older, newer = (
        _meeting(db_session, team, "기획 회의", 1),
        _meeting(db_session, team, "결제 회의", 2),
    )
    _report(db_session, older)
    _report(db_session, newer)
    service.claim_meeting_report(db_session, older, draft_id="rdr_a")

    response = client_for(_user(db_session, team)).get(f"/api/intelligence/meeting-reports/{team}")

    assert response.status_code == 200
    rows = response.json()
    assert [r["meeting_id"] for r in rows] == [newer, older]
    first, second = rows
    assert first["title"].startswith("결제 회의 · ")
    assert first["body"].startswith(BODY) and "📋" not in first["body"]
    assert (first["status"], second["status"]) == ("draft", "posted")
    assert second["posted_at"] is not None
    assert first["edited_by_name"] is None


def test_another_teams_reports_are_not_listed(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    _report(db_session, _meeting(db_session, other.id, "남의 회의", 1))

    rows = (
        client_for(_user(db_session, team)).get(f"/api/intelligence/meeting-reports/{team}").json()
    )

    assert rows == []


def test_someone_outside_the_team_is_refused(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    _report(db_session, _meeting(db_session, team, "결제 회의", 1))

    response = client_for(_user(db_session, None)).get(f"/api/intelligence/meeting-reports/{team}")

    assert response.status_code == 403


# --- edit ------------------------------------------------------------------------


def test_a_member_edits_a_draft_and_is_recorded_as_its_editor(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting, draft_id="rdr_a")
    editor = _user(db_session, team, name="박재경")

    response = client_for(editor).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    assert response.status_code == 200
    assert response.json()["body"] == "✅ 고친 본문"
    assert response.json()["edited_by_name"] == "박재경"
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert row.body_markdown.startswith("📋 결제 회의 · ")  # the header stays E's
    assert "\n\n✅ 고친 본문\n\n" in row.body_markdown
    assert row.edited_by == editor.id and row.edited_at is not None
    # A person's text is a new draft: the model's approval does not cover it.
    assert row.draft_id is not None and row.draft_id != "rdr_a"


def test_a_posted_report_is_not_edited(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    service.claim_meeting_report(db_session, meeting, draft_id="rdr_a")

    response = client_for(_user(db_session, team)).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    assert response.status_code == 409


def test_personal_data_in_an_edit_is_refused_by_category(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)

    response = client_for(_user(db_session, team)).put(
        f"/api/intelligence/meeting-reports/{meeting}",
        json={"body": "연락처 010-1234-5678 로 주세요"},
    )

    assert response.status_code == 422
    assert "010-1234-5678" not in response.text  # categories only, never the text
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and "010-1234-5678" not in row.body_markdown


def test_an_edit_over_the_cap_or_empty_is_refused(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    client = client_for(_user(db_session, team))

    too_long = client.put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "가" * 3000}
    )
    empty = client.put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": "   "})

    assert (too_long.status_code, empty.status_code) == (422, 422)


def test_the_footer_says_who_edited_and_is_not_part_of_the_editable_body(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """ "자동 생성된 리포트입니다." would be false on a person's text."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    member = _user(db_session, team, name="박재경")
    client = client_for(member)

    before = client.get(f"/api/intelligence/meeting-reports/{team}").json()[0]
    after = client.put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    ).json()

    assert "자동 생성" not in before["body"] and before["footer"].startswith("자동 생성")
    assert after["body"] == "✅ 고친 본문"
    assert "박재경" in after["footer"]


def test_a_stale_edit_is_refused_instead_of_overwriting_a_newer_one(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    first, second = _user(db_session, team, "가"), _user(db_session, team, "나")
    seen = client_for(first).get(f"/api/intelligence/meeting-reports/{team}").json()[0]

    client_for(second).put(
        f"/api/intelligence/meeting-reports/{meeting}",
        json={"body": "✅ 나의 수정", "base_updated_at": seen["updated_at"]},
    )
    stale = client_for(first).put(
        f"/api/intelligence/meeting-reports/{meeting}",
        json={"body": "✅ 가의 수정", "base_updated_at": seen["updated_at"]},
    )

    assert stale.status_code == 409
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and "나의 수정" in row.body_markdown


def test_a_rerun_overwrites_an_edited_draft_and_clears_the_editor(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """A republished event reruns the Report (#556); its draft replaces a person's."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    client_for(_user(db_session, team, "박재경")).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    _report(db_session, meeting, draft_id="rdr_rerun")

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and "고친 본문" not in row.body_markdown
    assert (row.edited_by, row.edited_at) == (None, None)


def test_an_edit_ends_the_approval_given_for_the_models_text(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approved and queued, then edited before the worker claims it: nothing posts."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting, draft_id="rdr_model")
    client_for(_user(db_session, team)).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    import contextlib

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks.deliver_meeting_report, "apply_async", lambda _args: None)
    refused = tools.publish_meeting_report(team, meeting, draft_id="rdr_model")
    assert refused["ok"] is False and refused["reason"] == "draft not current"
    with pytest.raises(AutuneError):
        service.claim_meeting_report(db_session, meeting, draft_id="rdr_model")


def test_the_last_editor_posts_their_edit_from_the_card(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autune_intelligence import router as router_module

    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        router_module.enqueue,
        "deliver_meeting_report",
        lambda meeting_id, draft_id: sent.append((meeting_id, draft_id)),
    )
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    editor = _user(db_session, team, "박재경")
    client = client_for(editor)
    edited = client.put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    ).json()
    assert edited["can_post"] is True

    response = client.post(f"/api/intelligence/meeting-reports/{meeting}/post")

    assert response.status_code == 202
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert sent == [(meeting, row.draft_id)]  # ids only (#275); the claim checks the id again


def test_only_the_last_editor_posts(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autune_intelligence import router as router_module

    monkeypatch.setattr(router_module.enqueue, "deliver_meeting_report", lambda *_args: None)
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    client_for(_user(db_session, team, "박재경")).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )
    other = client_for(_user(db_session, team, "문민재"))

    assert other.get(f"/api/intelligence/meeting-reports/{team}").json()[0]["can_post"] is False
    assert other.post(f"/api/intelligence/meeting-reports/{meeting}/post").status_code == 403


def test_a_models_draft_is_posted_through_approval_not_the_card(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)

    response = client_for(_user(db_session, team)).post(
        f"/api/intelligence/meeting-reports/{meeting}/post"
    )

    assert response.status_code == 409


def test_someone_outside_the_team_cannot_edit(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)

    outsider = client_for(_user(db_session, None))
    response = outsider.put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )
    unknown = outsider.put(
        "/api/intelligence/meeting-reports/mtg_unknown", json={"body": "✅ 고친 본문"}
    )

    # The same answer either way, so a meeting id's existence is not revealed.
    assert (response.status_code, unknown.status_code) == (404, 404)


def test_deleting_the_editor_keeps_the_report_without_their_id(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """The editor is a per-person record; it goes with the person, the report stays."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    editor = _user(db_session, team, name="박재경")
    client_for(editor).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    db_session.execute(sa.delete(TeamMember).where(TeamMember.user_id == editor.id))
    db_session.execute(sa.delete(User).where(User.id == editor.id))
    db_session.expire_all()

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.edited_by is None
    # The name was never stored with the text, so it goes with the account
    # (invariant 11, #642 review): not in the row, not in what the card reads.
    assert "박재경" not in row.body_markdown
    reader = client_for(_user(db_session, team, "문민재"))
    [shown] = reader.get(f"/api/intelligence/meeting-reports/{team}").json()
    assert "박재경" not in shown["footer"] and "박재경" not in shown["body"]


def test_the_editors_name_is_added_when_read_and_posted_not_stored(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    editor = _user(db_session, team, name="박재경")
    edited = (
        client_for(editor)
        .put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"})
        .json()
    )

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and "박재경" not in row.body_markdown
    assert "박재경" in edited["footer"]
    claimed = service.claim_meeting_report(db_session, meeting, draft_id=row.draft_id)
    assert claimed is not None and claimed.body_markdown.endswith("박재경님이 고쳤습니다.")


def test_a_name_that_looks_like_personal_data_is_left_out_of_the_post(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """A display name the outbound check would refuse must not make the post unsendable."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    editor = _user(db_session, team, name="010-1234-5678")
    response = client_for(editor).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )
    assert response.status_code == 200  # the name is not the editor's text to fix

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    claimed = service.claim_meeting_report(db_session, meeting, draft_id=row.draft_id)
    assert claimed is not None and "010-1234-5678" not in claimed.body_markdown


def test_posting_a_posted_report_is_a_conflict_and_an_outsider_gets_not_found(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autune_intelligence import router as router_module

    monkeypatch.setattr(router_module.enqueue, "deliver_meeting_report", lambda *_args: None)
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    editor = _user(db_session, team, "박재경")
    client_for(editor).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    service.claim_meeting_report(db_session, meeting, draft_id=row.draft_id)

    posted = client_for(editor).post(f"/api/intelligence/meeting-reports/{meeting}/post")
    outsider = client_for(_user(db_session, None)).post(
        f"/api/intelligence/meeting-reports/{meeting}/post"
    )

    assert (posted.status_code, outsider.status_code) == (409, 404)


def test_an_edit_between_posting_and_the_claim_posts_nothing(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued for one draft, edited again before the worker claims it."""
    from autune_intelligence import router as router_module

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        router_module.enqueue,
        "deliver_meeting_report",
        lambda meeting_id, draft_id: queued.append((meeting_id, draft_id)),
    )
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    client = client_for(_user(db_session, team, "박재경"))
    client.put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 첫 수정"})
    client.post(f"/api/intelligence/meeting-reports/{meeting}/post")
    client.put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 두 번째 수정"})

    [(queued_meeting, queued_draft)] = queued
    with pytest.raises(AutuneError):
        service.claim_meeting_report(db_session, queued_meeting, draft_id=queued_draft)


def test_the_cap_counts_the_text_as_slack_will_receive_it(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """An "&" goes out as "&amp;": a body under the cap before escaping can be over it after."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)

    response = client_for(_user(db_session, team)).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "&" * 700}
    )

    assert response.status_code == 422


def test_a_last_paragraph_that_reads_like_the_footer_stays_in_the_body(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """Only E's exact footers are split off; a person's own line is not dropped."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    client = client_for(_user(db_session, team, "박재경"))
    body = "✅ 고친 본문\n\n자동 생성 기능은 다음 주에 다시 봅니다"

    first = client.put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": body}).json()
    again = client.put(
        f"/api/intelligence/meeting-reports/{meeting}",
        json={"body": first["body"], "base_updated_at": first["updated_at"]},
    ).json()

    assert first["body"] == body and again["body"] == body
    assert again["footer"].endswith("박재경님이 고쳤습니다.")
