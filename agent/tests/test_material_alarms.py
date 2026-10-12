"""A stopped upload reaches the team's approvers, once, and only them (#817, #1201).

The alarm rows are written by module B's own upload, refusing a marked file,
not built by hand: what this layer reads is what that writer leaves.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_agent import router as routes
from autune_agent import tasks
from autune_agent.main import material_alarms
from autune_agent.main.material_alarms import (
    ALARM_SCOPES,
    TELL_EVERY,
    tell_approvers_of_stopped_uploads,
    tell_team,
)
from autune_agent.main.notify import APPROVALS_PATH
from autune_agent.models import AgentApprover, AgentRun
from autune_core import (
    AutuneError,
    Base,
    Team,
    TeamMember,
    User,
    current_user,
    get_session,
    get_settings,
)
from autune_core.errors import PrivacyViolationError
from autune_core.periodic import is_periodic_task_name, schedule_of
from autune_extraction import materials
from autune_extraction.materials import ConfidentialFileError
from autune_extraction.models import ExtMaterial, ExtMaterialAlarm
from autune_integrations.errors import SlackRecipientNotLinkedError
from autune_integrations.fakes import FakeSlack

NOON = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
FILE_NAME = "인수 검토 보고서.txt"
MARKED = "[대외비] 인수 검토 보고서\n\n대상 회사의 3분기 매출과 인수 가격 범위를 정리했다."
CONFIDENTIAL_EN = (
    "CONFIDENTIAL\n\nAcquisition review: the target's third quarter and the price range."
)


@pytest.fixture(autouse=True)
def _alarm_table(session: Session) -> None:
    # The refusal comes after the upload has counted the team's shelf.
    Base.metadata.create_all(
        session.get_bind(), tables=[ExtMaterial.__table__, ExtMaterialAlarm.__table__]
    )


def _person(session: Session, team_id: str, email: str, *scopes: str) -> str:
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    session.add(TeamMember(team_id=team_id, user_id=user.id))
    for scope in scopes:
        session.add(AgentApprover(team_id=team_id, user_id=user.id, scope=scope))
    session.flush()
    return user.id


def _stop_an_upload(
    session: Session, team_id: str, *, at: datetime = NOON, text: str = MARKED
) -> ExtMaterialAlarm:
    """Module B refuses a marked file and leaves its alarm."""
    before = {alarm.id for alarm in materials.pending_alarms(session, team_id)}
    with pytest.raises(ConfidentialFileError):
        materials.store_upload(
            session, team_id, title="검토 자료", file_name=FILE_NAME, data=text.encode()
        )
    (new,) = [a for a in materials.pending_alarms(session, team_id) if a.id not in before]
    # The writer reads the clock itself; a test here needs to say when.
    new.created_at = at
    session.commit()
    return new


def _link() -> str:
    return get_settings().web_base_url.rstrip("/") + APPROVALS_PATH


def _aware(moment: datetime | None) -> datetime | None:
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=UTC)


# --- the DM -----------------------------------------------------------------------


def test_this_layer_reads_module_b_through_its_tools_and_nothing_else() -> None:
    """The layer's owner on #1201: ``main/`` reads a module through its
    ``tools.py``, so a change inside module B does not break this layer."""
    tree = ast.parse(Path(material_alarms.__file__).read_text(encoding="utf-8"))
    from_modules = sorted(
        {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("autune_extraction")
        }
    )
    plain = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("autune_extraction")
    ]
    assert (from_modules, plain) == (["autune_extraction.tools"], [])


def test_who_is_told_is_the_holders_of_any() -> None:
    # #1201 question 2, as the layer's owner answered it (2026-10-12): ``any`` only.
    assert {"any"} == ALARM_SCOPES


def test_each_approver_gets_one_dm_and_nobody_else_gets_any(
    session: Session, team: dict[str, str]
) -> None:
    first = _person(session, team["team"], "a@example.com", "any")
    second = _person(session, team["team"], "b@example.com", "any", "report")
    _person(session, team["team"], "narrow@example.com", "report", "research")
    _stop_an_upload(session, team["team"])
    slack = FakeSlack()

    sent = tell_team(session, team["team"], slack=slack)

    assert sent == sorted([first, second])
    assert [m.channel for m in slack.sent] == sorted([first, second])
    assert all(m.is_dm for m in slack.sent)
    # team["member"] holds no scope, and the narrow approver holds no ``any``.


def test_the_message_holds_a_count_and_a_link_only(session: Session, team: dict[str, str]) -> None:
    _person(session, team["team"], "a@example.com", "any")
    alarm = _stop_an_upload(session, team["team"], text=CONFIDENTIAL_EN)
    slack = FakeSlack()

    tell_team(session, team["team"], slack=slack)

    (message,) = slack.sent
    assert (
        message.text == f"기밀·대외비 표시가 있어 받지 않은 자료 업로드가 1건 있습니다. {_link()}"
    )
    # The time and the kind stay behind the page's sign-in; the rest was never kept.
    for kept_back in (alarm.marking, "english", "12:00", "10-09", alarm.id, FILE_NAME, "검토 자료"):
        assert kept_back not in message.text


def test_an_alarm_is_told_once_and_a_later_one_tells_again_with_all_that_wait(
    session: Session, team: dict[str, str]
) -> None:
    _person(session, team["team"], "a@example.com", "any")
    first = _stop_an_upload(session, team["team"])
    slack = FakeSlack()
    told_when = NOON + timedelta(minutes=40)

    assert tell_approvers_of_stopped_uploads(session, slack=slack, now=told_when) == 1
    session.rollback()  # the task committed it: nothing is left to undo
    assert _aware(first.told_at) == told_when
    assert len(slack.sent) == 1

    # Nothing new: the next runs send nothing and move nothing.
    assert (
        tell_approvers_of_stopped_uploads(session, slack=slack, now=NOON + timedelta(hours=2)) == 0
    )
    assert len(slack.sent) == 1 and _aware(first.told_at) == told_when
    # Asked about that one team by name, too: an alarm that was told is not news.
    assert tell_team(session, team["team"], slack=slack) == []
    assert len(slack.sent) == 1

    # A second stopped upload: one DM more, saying both wait; only the new one is marked.
    second = _stop_an_upload(session, team["team"], at=NOON + timedelta(hours=3))
    later = NOON + timedelta(hours=4)
    assert tell_approvers_of_stopped_uploads(session, slack=slack, now=later) == 1
    assert "2건" in slack.sent[-1].text and len(slack.sent) == 2
    assert _aware(first.told_at) == told_when and _aware(second.told_at) == later


def test_an_alarm_stays_untold_until_somebody_can_be_reached(
    session: Session, team: dict[str, str]
) -> None:
    alarm = _stop_an_upload(session, team["team"])
    slack = FakeSlack()

    # Nobody holds ``any`` yet.
    _person(session, team["team"], "narrow@example.com", "report")
    assert tell_approvers_of_stopped_uploads(session, slack=slack) == 0
    assert slack.sent == [] and alarm.told_at is None

    # The approver has not linked Slack.
    approver = _person(session, team["team"], "a@example.com", "any")

    class Unlinked(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
            raise SlackRecipientNotLinkedError(user_id)

    assert tell_approvers_of_stopped_uploads(session, slack=Unlinked()) == 0
    assert alarm.told_at is None

    # Now they can be reached: the run after that tells them.
    assert tell_approvers_of_stopped_uploads(session, slack=slack) == 1
    assert [m.channel for m in slack.sent] == [approver]
    assert alarm.told_at is not None


def test_one_approver_reached_is_enough_and_the_other_is_not_messaged_later(
    session: Session, team: dict[str, str]
) -> None:
    # The one whose Slack fails is the first to be tried, whatever the ids are.
    broken, fine = sorted(
        _person(session, team["team"], f"{name}@example.com", "any") for name in "ab"
    )
    alarm = _stop_an_upload(session, team["team"])

    class Flaky(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
            if user_id == broken:
                raise RuntimeError("slack said no")
            return super().send_dm(user_id, text, blocks)

    slack = Flaky()
    assert tell_team(session, team["team"], slack=slack) == [fine]
    assert alarm.told_at is not None
    assert tell_approvers_of_stopped_uploads(session, slack=slack) == 0
    assert [m.channel for m in slack.sent] == [fine]


def test_a_team_without_slack_is_told_nothing_and_the_alarm_stays_untold(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[tuple[str, str]] = []

    def no_integration(session: Session, team_id: str, service: str) -> None:
        looked_up.append((team_id, service))

    monkeypatch.setattr(material_alarms, "load_integration", no_integration)
    _person(session, team["team"], "a@example.com", "any")
    alarm = _stop_an_upload(session, team["team"])

    assert tell_approvers_of_stopped_uploads(session) == 0
    assert looked_up == [(team["team"], "slack")]
    assert alarm.told_at is None


def test_an_approver_who_left_the_team_is_not_told(session: Session, team: dict[str, str]) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["outsider"], scope="any"))
    session.flush()
    alarm = _stop_an_upload(session, team["team"])
    slack = FakeSlack()

    assert tell_team(session, team["team"], slack=slack) == []
    assert slack.sent == [] and alarm.told_at is None


def test_one_teams_failure_leaves_the_other_told_and_the_log_names_nobody(
    session: Session, team: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    ours = _person(session, team["team"], "a@example.com", "any")
    theirs = _person(session, other.id, "b@example.com", "any")
    mine = _stop_an_upload(session, team["team"])
    other_alarm = _stop_an_upload(session, other.id)

    class OneTeamDown(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
            if user_id == theirs:
                raise RuntimeError("slack said no")
            return super().send_dm(user_id, text, blocks)

    slack = OneTeamDown()
    with caplog.at_level(logging.DEBUG), capture_logs() as lines:
        assert tell_approvers_of_stopped_uploads(session, slack=slack) == 1

    assert [m.channel for m in slack.sent] == [ours]
    assert mine.told_at is not None and other_alarm.told_at is None
    written = caplog.text + repr(lines)
    assert "agent_material_alarms_told teams=1 unreachable=1" in written
    for kept_out in (ours, theirs, mine.id, other_alarm.id, team["team"], other.id):
        assert kept_out not in written


def test_a_team_whose_telling_raises_is_rolled_back_and_the_next_team_is_told(
    session: Session,
    team: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    # The team that fails is the one the run reaches first, whatever the ids are.
    broken_team, fine_team = sorted([team["team"], other.id])
    _person(session, broken_team, "a@example.com", "any")
    theirs = _person(session, fine_team, "b@example.com", "any")
    mine = _stop_an_upload(session, broken_team)
    other_alarm = _stop_an_upload(session, fine_team)
    real = material_alarms.tell_team

    def breaks_for_one(session: Session, team_id: str, **rest: object) -> list[str]:
        if team_id == broken_team:
            # Half done when it fails: the mark is in the session, uncommitted.
            materials.mark_alarms_told(session, team_id, [mine.id])
            raise RuntimeError(f"could not tell {team_id}")
        return real(session, team_id, **rest)  # type: ignore[arg-type]

    monkeypatch.setattr(material_alarms, "tell_team", breaks_for_one)
    slack = FakeSlack()
    with caplog.at_level(logging.DEBUG):
        assert tell_approvers_of_stopped_uploads(session, slack=slack) == 1

    # No rollback of the test's own here: it would hide a missing one.
    assert [m.channel for m in slack.sent] == [theirs]
    assert mine.told_at is None, "the failed team's half-made mark was rolled back"
    assert other_alarm.told_at is not None
    assert "agent_material_alarm_team_failed error=RuntimeError" in caplog.text
    # The error's class, not its message: a message can carry anything.
    assert broken_team not in caplog.text and "could not tell" not in caplog.text


def test_a_privacy_violation_is_raised_not_swallowed(
    session: Session, team: dict[str, str]
) -> None:
    _person(session, team["team"], "a@example.com", "any")
    alarm = _stop_an_upload(session, team["team"])

    class Refusing(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
            raise PrivacyViolationError("slack")

    with pytest.raises(PrivacyViolationError):
        tell_approvers_of_stopped_uploads(session, slack=Refusing())
    assert alarm.told_at is None


def test_the_dm_goes_through_the_outbound_check(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # FakeSlack runs check_outbound on the body, as SlackClient does. A count
    # and a link pass it; a number shaped like a phone number in the same
    # message would not, which is what holds the message to its two parts.
    _person(session, team["team"], "a@example.com", "any")
    _stop_an_upload(session, team["team"])
    monkeypatch.setattr(
        material_alarms, "APPROVALS_PATH", "/approvals?call=010-2345-6789", raising=True
    )

    with pytest.raises(PrivacyViolationError):
        tell_team(session, team["team"], slack=FakeSlack())


def test_the_task_is_on_beats_schedule_and_runs_the_telling(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert is_periodic_task_name(tasks.tell_material_alarms.name)
    assert tasks.tell_material_alarms.name == "autune.agent.periodic.tell_material_alarms"
    assert schedule_of(tasks.tell_material_alarms) == TELL_EVERY == timedelta(hours=1)

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    ran: list[Session] = []
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "tell_approvers_of_stopped_uploads", ran.append)
    tasks.tell_material_alarms()
    assert ran == [session]


# --- the routes -------------------------------------------------------------------


def _client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    async def _error(_: object, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app)


URL = "/api/agent/material-alarms"


def test_an_approver_sees_when_and_which_kind_and_nothing_else(
    session: Session, team: dict[str, str]
) -> None:
    approver = _person(session, team["team"], "a@example.com", "any")
    korean = _stop_an_upload(session, team["team"])
    english = _stop_an_upload(
        session, team["team"], at=NOON + timedelta(hours=1), text=CONFIDENTIAL_EN
    )
    tell_team(session, team["team"], slack=FakeSlack())  # told_at is set and is not shown

    for params in ({"team_id": team["team"]}, {}):
        answer = _client(session, approver).get(URL, params=params)
        assert answer.status_code == 200
        body = answer.json()
        assert [set(row) for row in body] == [{"id", "team_id", "marking", "created_at"}] * 2
        assert [(row["id"], row["marking"]) for row in body] == [
            (korean.id, "korean_marking"),
            (english.id, "english_marking"),
        ]
        assert {row["team_id"] for row in body} == {team["team"]}
        assert body[0]["created_at"].startswith("2026-10-09T12:00:00")
        assert FILE_NAME not in answer.text and "검토 자료" not in answer.text


def test_only_an_approver_of_that_team_sees_its_alarms(
    session: Session, team: dict[str, str]
) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    approver = _person(session, team["team"], "a@example.com", "any")
    narrow = _person(session, team["team"], "narrow@example.com", "report")
    elsewhere = _person(session, other.id, "b@example.com", "any")
    session.add(TeamMember(team_id=team["team"], user_id=elsewhere))  # a plain member here
    session.flush()
    _stop_an_upload(session, team["team"])
    theirs = _stop_an_upload(session, other.id)

    assert len(_client(session, approver).get(URL).json()) == 1
    # A member without ``any``: an empty list, as a team with no alarm gives.
    for user_id in (narrow, team["member"]):
        for params in ({"team_id": team["team"]}, {}):
            answer = _client(session, user_id).get(URL, params=params)
            assert (answer.status_code, answer.json()) == (200, [])
    # An approver elsewhere who is a plain member here sees their own team's only.
    assert [row["id"] for row in _client(session, elsewhere).get(URL).json()] == [theirs.id]
    assert _client(session, elsewhere).get(URL, params={"team_id": team["team"]}).json() == []
    # Not on the team at all.
    assert _client(session, approver).get(URL, params={"team_id": other.id}).status_code == 403
    assert (
        _client(session, team["outsider"]).get(URL, params={"team_id": team["team"]}).status_code
        == 403
    )
    # An approver row kept by someone who left counts for nothing.
    session.add(AgentApprover(team_id=team["team"], user_id=team["outsider"], scope="any"))
    session.flush()
    assert _client(session, team["outsider"]).get(URL).json() == []


def test_acknowledging_deletes_the_alarm_and_records_nobody(
    session: Session, team: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    approver = _person(session, team["team"], "a@example.com", "any")
    first = _stop_an_upload(session, team["team"])
    second = _stop_an_upload(session, team["team"], at=NOON + timedelta(hours=1))
    first_id = first.id

    with caplog.at_level(logging.DEBUG), capture_logs() as lines:
        answer = _client(session, approver).post(
            f"{URL}/{first_id}/acknowledge", params={"team_id": team["team"]}
        )

    assert (answer.status_code, answer.content) == (204, b"")
    session.rollback()  # the route committed the deletion
    assert [a.id for a in materials.pending_alarms(session, team["team"])] == [second.id]
    assert session.scalars(select(AgentRun)).all() == []
    # The server's own lines; the test client's line about its request is not one.
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("autune")]
    written = repr(ours) + repr(lines)
    assert [line["event"] for line in lines] == ["extraction_material_alarm_acknowledged"]
    assert approver not in written and first_id not in written
    # Nothing in the row that remains, or anywhere in its table, could name them.
    assert {c.name for c in ExtMaterialAlarm.__table__.columns} == {
        "id",
        "team_id",
        "marking",
        "created_at",
        "told_at",
    }


def test_nobody_but_the_teams_approver_acknowledges(session: Session, team: dict[str, str]) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    approver = _person(session, team["team"], "a@example.com", "any")
    narrow = _person(session, team["team"], "narrow@example.com", "report")
    alarm = _stop_an_upload(session, team["team"])
    theirs = _stop_an_upload(session, other.id)
    here = {"team_id": team["team"]}

    def post(user_id: str, alarm_id: str, params: dict[str, str]) -> int:
        return (
            _client(session, user_id)
            .post(f"{URL}/{alarm_id}/acknowledge", params=params)
            .status_code
        )

    assert post(narrow, alarm.id, here) == 403
    assert post(team["member"], alarm.id, here) == 403
    assert post(team["outsider"], alarm.id, here) == 403
    refused = _client(session, team["outsider"]).post(f"{URL}/{alarm.id}/acknowledge", params=here)
    assert refused.json()["error"]["message"] == "not a member of this team"
    # Someone who is not an approver is refused the same way for an id that is not there.
    assert post(narrow, "mal_unknown", here) == 403
    # An approver of this team cannot reach another team's alarm, by either team id.
    assert post(approver, theirs.id, here) == 404
    assert post(approver, theirs.id, {"team_id": other.id}) == 403
    assert post(approver, "mal_unknown", here) == 404
    assert _client(session, approver).post(f"{URL}/{alarm.id}/acknowledge").status_code == 422

    assert [a.id for a in materials.pending_alarms(session, team["team"])] == [alarm.id]
    assert [a.id for a in materials.pending_alarms(session, other.id)] == [theirs.id]
    assert post(approver, alarm.id, here) == 204
    assert post(approver, alarm.id, here) == 404
