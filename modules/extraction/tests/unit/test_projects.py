"""A team's projects, and which project each decision and item is about (2026-10-04).

The rules under test: a team with one project puts everything there, a row's
own lines decide when they name one project, otherwise the project named last
before it, a line naming two settles nothing, a Latin name matches only as a
word, only consented lines are read, and a project a person chose is never
changed by the rules again. Then the routes: only the team's members see and
change its projects, and a row can only go to a project of its own team.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance, get_session
from autune_core.errors import AutuneError
from autune_extraction import projects, service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtDecision,
    ExtDecisionSource,
    ExtProject,
)
from autune_extraction.projects import Project, choose
from autune_extraction.router import router

from .conftest import READER, sign_in

MEETING = "mtg_1"
TEAM = "team_1"
PREFIX = "/api/extraction"

A = Project("prj_a", ("오튠", "Autune"))
B = Project("prj_b", ("App",))


def lines(*texts: str) -> list[tuple[str, str]]:
    return [(f"utt_{n}", text) for n, text in enumerate(texts)]


# --- the rules -------------------------------------------------------------------


def test_one_project_takes_everything() -> None:
    assert choose([A], lines("아무 말"), ["utt_0"]) == "prj_a"


def test_no_projects_places_nothing() -> None:
    assert choose([], lines("오튠 배포"), ["utt_0"]) is None


def test_the_rows_own_line_decides() -> None:
    said = lines("App 얘기부터 할게요", "오튠은 금요일에 배포하죠")
    assert choose([A, B], said, ["utt_1"]) == "prj_a"


def test_the_project_named_last_before_the_row_holds() -> None:
    said = lines("이제 오튠 얘기할게요", "로그인 오류가 있었죠", "그건 제가 고칠게요")
    assert choose([A, B], said, ["utt_2"]) == "prj_a"


def test_a_line_naming_two_projects_settles_nothing() -> None:
    said = lines("오튠 얘기", "오튠이랑 App 둘 다 늦어요", "제가 정리할게요")
    assert choose([A, B], said, ["utt_2"]) is None


def test_a_latin_name_matches_only_as_a_word() -> None:
    said = lines("happy path만 확인했어요", "제가 정리할게요")
    assert choose([A, B], said, ["utt_1"]) is None
    assert choose([A, B], lines("app 출시는 다음 주예요"), ["utt_0"]) == "prj_b"


def test_nothing_named_before_is_unassigned() -> None:
    assert choose([A, B], lines("일정 얘기", "제가 할게요"), ["utt_1"]) is None


# --- the meeting ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
        s.add(Meeting(id="mtg_other", team_id="team_2", title="다른 팀"))
        s.add(Participant(id="par_yes", meeting_id=MEETING, speaker_label="A", consented=True))
        s.add(Participant(id="par_no", meeting_id=MEETING, speaker_label="B", consented=False))
        said = [
            ("utt_0", "par_yes", "이제 오튠 얘기할게요"),
            ("utt_1", "par_yes", "로그인은 제가 고칠게요"),
            ("utt_2", "par_no", "App 얘기로 넘어가죠"),  # not consented: never read
            ("utt_3", "par_yes", "배포는 금요일로 하죠"),
        ]
        for n, (uid, who, text) in enumerate(said):
            s.add(
                Utterance(
                    id=uid,
                    meeting_id=MEETING,
                    participant_id=who,
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
        s.add(ExtProject(id="prj_a", team_id=TEAM, name="Autune", aliases="오튠"))
        s.add(ExtProject(id="prj_b", team_id=TEAM, name="App", aliases=""))
        s.add(
            ExtActionItem(
                id="act_1",
                meeting_id=MEETING,
                description="로그인 고치기",
                status="needs_confirmation",
                confidence=0.9,
                origin="model",
            )
        )
        s.add(ExtActionItemSource(action_item_id="act_1", utterance_id="utt_1"))
        s.add(
            ExtDecision(
                id="dec_1",
                meeting_id=MEETING,
                statement="배포는 금요일로 한다",
                confidence=0.9,
                origin="model",
            )
        )
        s.add(ExtDecisionSource(decision_id="dec_1", utterance_id="utt_3", position=0))
        s.flush()
        yield s


def test_a_meeting_is_placed_from_its_consented_lines(session: Session) -> None:
    projects.assign_meeting(session, MEETING)

    assert session.get(ExtActionItem, "act_1").project_id == "prj_a"  # type: ignore[union-attr]
    # utt_2 named App but its speaker did not consent: Autune is still in force.
    assert session.get(ExtDecision, "dec_1").project_id == "prj_a"  # type: ignore[union-attr]


def test_a_project_a_person_chose_stays(session: Session) -> None:
    item = session.get(ExtActionItem, "act_1")
    assert item is not None
    projects.place(session, item, "prj_b")

    projects.assign_meeting(session, MEETING)

    assert item.project_id == "prj_b" and item.project_by_person


def test_a_moved_item_survives_extracting_the_meeting_again(session: Session) -> None:
    item = session.get(ExtActionItem, "act_1")
    assert item is not None
    projects.place(session, item, "prj_b")

    rebuilt = service.build_action_items(session, meeting_id=MEETING, utterances=[], classified=[])

    assert rebuilt is None, "the move is a correction, so the draft is kept"
    kept = session.get(ExtActionItem, "act_1")
    assert kept is not None and kept.project_id == "prj_b" and kept.project_by_person
    assert [(e.kind, e.fields) for e in service.edit_history(session, "act_1")] == [
        ("edited", ["project_id"])
    ]


def test_deleting_a_project_unassigns_its_rows(session: Session) -> None:
    projects.assign_meeting(session, MEETING)

    projects.delete_project(session, TEAM, "prj_a")

    assert session.get(ExtActionItem, "act_1").project_id is None  # type: ignore[union-attr]


# --- the routes -----------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    return TestClient(app)


def test_a_member_lists_adds_renames_and_deletes_projects(client: TestClient) -> None:
    created = client.post(
        f"{PREFIX}/projects?team_id={TEAM}",
        json={"name": "  데이터  플랫폼 ", "aliases": ["DP", "DP", " "], "jira_project_key": "dp"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "데이터 플랫폼"
    assert body["aliases"] == ["DP"]
    assert body["jira_project_key"] == "DP"

    renamed = client.put(
        f"{PREFIX}/projects/{body['id']}?team_id={TEAM}",
        json={"name": "데이터", "aliases": []},
    )
    assert renamed.json()["name"] == "데이터"

    names = [p["name"] for p in client.get(f"{PREFIX}/projects?meeting_id={MEETING}").json()]
    assert names == ["Autune", "App", "데이터"]

    assert client.delete(f"{PREFIX}/projects/{body['id']}?team_id={TEAM}").status_code == 204


def test_a_name_twice_and_a_bad_jira_key_are_refused(client: TestClient) -> None:
    taken = client.post(f"{PREFIX}/projects?team_id={TEAM}", json={"name": "App"})
    bad_key = client.post(
        f"{PREFIX}/projects?team_id={TEAM}", json={"name": "X", "jira_project_key": "1-2"}
    )

    assert taken.status_code == 409
    assert bad_key.status_code == 422


def test_another_teams_projects_are_not_there(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/projects?team_id=team_2").status_code == 404
    assert client.post(f"{PREFIX}/projects?team_id=team_2", json={"name": "X"}).status_code == 404


def test_a_row_goes_only_to_a_project_of_its_own_team(client: TestClient, session: Session) -> None:
    session.add(ExtProject(id="prj_x", team_id="team_2", name="남의 프로젝트", aliases=""))
    session.flush()

    placed = client.put(f"{PREFIX}/action-items/act_1/project", json={"project_id": "prj_b"})
    refused = client.put(f"{PREFIX}/decisions/dec_1/project", json={"project_id": "prj_x"})

    assert placed.status_code == 200 and placed.json()["project_id"] == "prj_b"
    assert refused.status_code == 422


def test_the_summary_carries_the_projects_and_places_again_on_request(
    client: TestClient,
) -> None:
    summary = client.post(f"{PREFIX}/summary/{MEETING}/projects/assign").json()

    assert [p["id"] for p in summary["projects"]] == ["prj_a", "prj_b"]
    assert [d["project_id"] for d in summary["decisions"]] == ["prj_a"]
    assert [i["project_id"] for i in summary["action_items"]] == ["prj_a"]


# --- names no project has yet ------------------------------------------------------


def _meeting_saying(
    session: Session,
    meeting_id: str,
    lines: list[str],
    *,
    days_ago: int = 1,
    status: str = "complete",
    unconsented: list[str] | None = None,
) -> None:
    """A meeting of TEAM, held ``days_ago``, where a consenting speaker said
    ``lines`` and one who did not consent said ``unconsented``."""
    session.add(
        Meeting(
            id=meeting_id,
            team_id=TEAM,
            title=meeting_id,
            started_at=datetime.now(UTC) - timedelta(days=days_ago),
            status=status,
        )
    )
    for who, consented, texts in (("y", True, lines), ("n", False, unconsented or [])):
        participant = f"par_{meeting_id}_{who}"
        session.add(
            Participant(
                id=participant, meeting_id=meeting_id, speaker_label=who, consented=consented
            )
        )
        for n, text in enumerate(texts):
            session.add(
                Utterance(
                    id=f"utt_{meeting_id}_{who}{n}",
                    meeting_id=meeting_id,
                    participant_id=participant,
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
    session.flush()


def test_words_said_in_several_meetings_that_no_project_has_are_suggested(
    client: TestClient, session: Session
) -> None:
    for n in range(3):
        _meeting_saying(
            session,
            f"mtg_s{n}",
            ["Payflow 결제에서 오튠 연동을 봤어요", "결제는 마이크로 서비스로"],
            unconsented=["Secretname 얘기"],
        )

    suggested = client.get(f"{PREFIX}/projects/suggestions?team_id={TEAM}").json()

    words = {s["word"]: s["count"] for s in suggested}
    assert words["Payflow"] == 3
    assert words["결제"] == 3, "said twice in each, counted once per meeting"
    assert words["연동"] == 3, "을 is always a particle"
    assert "마이크로" in words and "마이크" not in words, "no particle split it alone"
    assert "오튠" not in words, "already Autune's alias"
    assert "Secretname" not in words, "a speaker who did not consent is not read"
    assert "봤어요" not in words, "a verb names no project"
    assert set(suggested[0]) == {"word", "count"}, "words and counts, never a sentence"


def test_a_word_repeated_in_one_meeting_is_one_meeting(
    client: TestClient, session: Session
) -> None:
    _meeting_saying(session, "mtg_z", ["Zeta Zeta Zeta", "Zeta 다시 Zeta"])

    suggested = client.get(f"{PREFIX}/projects/suggestions?team_id={TEAM}").json()

    assert suggested == []


def test_team_members_and_people_addressed_are_never_suggested(
    client: TestClient, session: Session
) -> None:
    session.add(User(id="usr_mj", email="mj@example.com", display_name="문민재"))
    session.add(TeamMember(team_id=TEAM, user_id="usr_mj"))
    for n in range(3):
        _meeting_saying(
            session,
            f"mtg_n{n}",
            ["민재님이 결제 맡아주세요", "민재가 할게요", "지훈씨도 같이 봐요"],
        )

    suggested = client.get(f"{PREFIX}/projects/suggestions?team_id={TEAM}").json()

    words = {s["word"] for s in suggested}
    assert words == {"결제"}, words


def test_somebody_who_spoke_and_has_left_the_team_is_not_suggested_either(
    client: TestClient, session: Session
) -> None:
    """No membership, and a participant row in each meeting that still names
    them: the meeting's roster has them, the team's member list does not."""
    session.add(User(id="usr_gone", email="gone@example.com", display_name="한서윤"))
    for n in range(3):
        _meeting_saying(
            session, f"mtg_g{n}", ["서윤이 결제 맡아주세요", "서윤이 할게요", "서윤 자료 참고"]
        )
        session.add(
            Participant(
                id=f"par_mtg_g{n}_gone",
                meeting_id=f"mtg_g{n}",
                speaker_label="g",
                consented=True,
                user_id="usr_gone",
            )
        )
    session.flush()

    suggested = client.get(f"{PREFIX}/projects/suggestions?team_id={TEAM}").json()

    words = {s["word"] for s in suggested}
    assert not any(word.startswith("서윤") for word in words), words
    assert "결제" in words


def test_meetings_not_yet_held_or_failed_are_not_read(client: TestClient, session: Session) -> None:
    # Ten meetings booked ahead would otherwise fill the latest ten.
    for n in range(10):
        _meeting_saying(session, f"mtg_f{n}", [], days_ago=-(n + 1), status="scheduled")
    _meeting_saying(session, "mtg_failed", ["Omega"], status="failed")
    _meeting_saying(session, "mtg_x", ["Omega"], status="scheduled", days_ago=2)
    for n in range(3):
        _meeting_saying(session, f"mtg_h{n}", ["Payflow"], days_ago=n + 3)

    suggested = client.get(f"{PREFIX}/projects/suggestions?team_id={TEAM}").json()

    assert suggested == [{"word": "Payflow", "count": 3}]


def test_suggestions_are_for_the_teams_members_only(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/projects/suggestions?team_id=team_2").status_code == 404


def test_mine_lists_the_projects_of_my_teams_only(client: TestClient, session: Session) -> None:
    session.add(ExtProject(id="prj_x", team_id="team_2", name="남의 프로젝트", aliases=""))
    session.flush()

    mine = client.get(f"{PREFIX}/projects/mine").json()

    assert [p["id"] for p in mine] == ["prj_a", "prj_b"]


def test_a_project_says_its_team_so_two_of_one_name_can_be_told_apart(
    client: TestClient, session: Session
) -> None:
    """A name is unique within a team, not across them; the reader of
    ``/projects/mine`` is on several."""
    first = session.get(ExtProject, "prj_a")
    assert first is not None
    session.add(TeamMember(team_id="team_2", user_id=READER))
    session.add(ExtProject(id="prj_twin", team_id="team_2", name=first.name, aliases=""))
    session.flush()

    mine = client.get(f"{PREFIX}/projects/mine").json()

    twins = {p["id"]: p["team_id"] for p in mine if p["name"] == first.name}
    assert twins == {"prj_a": TEAM, "prj_twin": "team_2"}
    one = client.get(f"{PREFIX}/projects?team_id={TEAM}").json()
    assert {p["team_id"] for p in one} == {TEAM}
