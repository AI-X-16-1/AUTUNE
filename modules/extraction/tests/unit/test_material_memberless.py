"""A team nobody is on keeps nothing of its 자료 screen (#817; the owner, 2026-10-13).

Only a member can delete a material or change the team's keeping setting, so
a team left with no member -- its last member deleted their own account --
would hold its materials with nobody able to end them, and without end if it
had chosen to keep them. The hourly task deletes what module B keeps for such
a team: uploads with their masked text, links, alarm rows, the setting.

The rows are made the way they are really made: a member uploads through the
route, the route refuses a marked file and writes the alarm, a member switches
the setting. SQLite enforces no foreign key here, so the membership row is
deleted by hand; that it goes by itself when the account is deleted is
``tests/integration/test_material_memberless_pg.py``.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError
from autune_extraction import materials, tasks
from autune_extraction.config import get_settings
from autune_extraction.models import (
    ExtMaterial,
    ExtMaterialAlarm,
    ExtMaterialChunk,
    ExtMaterialSetting,
)
from autune_extraction.router import router

from .conftest import READER, sign_in

TEAM = "team_1"
OTHER = "team_2"
PREFIX = "/api/extraction"
BODY = "3분기 계획\n예산 1200만원\n" * 60
OWNED = (ExtMaterial, ExtMaterialChunk, ExtMaterialAlarm, ExtMaterialSetting)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    owned = {m.__tablename__ for m in OWNED}
    tables = [t for name, t in Base.metadata.tables.items() if name in shared | owned]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add_all([Team(id=TEAM, name="팀"), Team(id=OTHER, name="남의 팀")])
        s.flush()
        yield s


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(get_settings(), "material_upload", True)
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    return TestClient(app)


_nth = itertools.count(1)


def a_member_fills_the_shelf(client: TestClient, *, title: str = "계획") -> None:
    """One upload, one link, one refused file and the keeping setting, all
    through the routes a member uses."""
    at = f"?team_id={TEAM}"
    assert (
        client.post(
            f"{PREFIX}/materials/upload{at}",
            data={"title": title},
            files={"file": ("plan.txt", f"{BODY}{next(_nth)}".encode(), "text/plain")},
        ).status_code
        == 201
    )
    assert (
        client.post(f"{PREFIX}/materials{at}", json={"title": "링크", "link": "a" * 20}).status_code
        == 201
    )
    refused = client.post(
        f"{PREFIX}/materials/upload{at}",
        data={"title": "요금표"},
        files={"file": ("fees.txt", f"대외비\n{BODY}".encode(), "text/plain")},
    )
    assert refused.status_code >= 400
    assert (
        client.put(
            f"{PREFIX}/materials/settings{at}", json={"keep_until_deleted": True}
        ).status_code
        == 200
    )


def the_last_member_is_gone(session: Session, team: str = TEAM) -> None:
    session.execute(delete(TeamMember).where(TeamMember.team_id == team))
    session.flush()


def held(session: Session, team: str = TEAM) -> dict[str, int]:
    def count(model: type, column: object) -> int:
        return session.scalar(select(func.count()).select_from(model).where(column == team)) or 0

    return {
        "materials": count(ExtMaterial, ExtMaterial.team_id),
        "pieces": session.scalar(
            select(func.count())
            .select_from(ExtMaterialChunk)
            .join(ExtMaterial, ExtMaterial.id == ExtMaterialChunk.material_id)
            .where(ExtMaterial.team_id == team)
        )
        or 0,
        "alarms": count(ExtMaterialAlarm, ExtMaterialAlarm.team_id),
        "settings": count(ExtMaterialSetting, ExtMaterialSetting.team_id),
    }


def pieces_of(session: Session, material_ids: list[str]) -> int:
    """By the material's id, not through its row: a piece left behind after
    its material went has no row to be found through, and SQLite does not
    cascade."""
    return (
        session.scalar(
            select(func.count())
            .select_from(ExtMaterialChunk)
            .where(ExtMaterialChunk.material_id.in_(material_ids))
        )
        or 0
    )


NOTHING = {"materials": 0, "pieces": 0, "alarms": 0, "settings": 0}


def test_a_team_nobody_is_on_loses_everything_it_kept_even_kept_until_deleted(
    client: TestClient, session: Session
) -> None:
    a_member_fills_the_shelf(client)
    before = held(session)
    assert before["materials"] == 2 and before["pieces"] > 1
    assert before["alarms"] == 1 and before["settings"] == 1
    assert materials.keeps_until_deleted(session, TEAM)
    uploaded = list(session.scalars(select(ExtMaterial.id).where(ExtMaterial.team_id == TEAM)))
    assert pieces_of(session, uploaded) == before["pieces"]

    the_last_member_is_gone(session)

    assert materials.forget_memberless_team_materials(session) == 1
    assert held(session) == NOTHING
    assert pieces_of(session, uploaded) == 0
    # The team itself is module A's: this deletes no shared row.
    assert session.get(Team, TEAM) is not None


def test_a_team_with_one_member_keeps_all_of_it(client: TestClient, session: Session) -> None:
    a_member_fills_the_shelf(client)
    before = held(session)

    assert materials.forget_memberless_team_materials(session) == 0
    assert held(session) == before


def test_only_the_team_nobody_is_on_is_emptied(client: TestClient, session: Session) -> None:
    a_member_fills_the_shelf(client)
    session.add(TeamMember(team_id=OTHER, user_id="someone_else"))
    theirs = materials.store_upload(
        session, OTHER, title="남의 계획", file_name="plan.txt", data=f"{BODY}남".encode()
    ).row
    the_last_member_is_gone(session)

    assert materials.forget_memberless_team_materials(session) == 1
    assert held(session) == NOTHING
    assert session.get(ExtMaterial, theirs.id) is not None
    assert held(session, OTHER)["pieces"] > 1


def test_one_of_two_members_leaving_changes_nothing(client: TestClient, session: Session) -> None:
    a_member_fills_the_shelf(client)
    session.add(TeamMember(team_id=TEAM, user_id="second"))
    session.flush()
    before = held(session)
    session.execute(delete(TeamMember).where(TeamMember.user_id == READER))

    assert materials.forget_memberless_team_materials(session) == 0
    assert held(session) == before


def test_a_team_that_never_kept_anything_is_not_counted(session: Session) -> None:
    assert materials.forget_memberless_team_materials(session) == 0


@pytest.mark.parametrize("left", ["alarm", "setting"])
def test_an_alarm_or_a_setting_alone_goes_too(session: Session, left: str) -> None:
    if left == "alarm":
        with pytest.raises(materials.ConfidentialFileError):
            materials.store_upload(
                session, TEAM, title="요금표", file_name="fees.txt", data=f"대외비\n{BODY}".encode()
            )
    else:
        materials.set_keep_until_deleted(session, TEAM, True)
    session.flush()

    assert materials.forget_memberless_team_materials(session) == 1
    assert held(session) == NOTHING


def test_the_log_has_the_team_and_counts_and_nothing_a_member_typed(
    client: TestClient, session: Session
) -> None:
    a_member_fills_the_shelf(client, title="김민수 평가")
    the_last_member_is_gone(session)

    with capture_logs() as lines:
        materials.forget_memberless_team_materials(session)

    assert lines == [
        {
            "event": "extraction_memberless_team_materials_forgotten",
            "log_level": "info",
            "team_id": TEAM,
            "materials": 2,
            "alarms": 1,
        }
    ]


def test_a_call_empties_at_most_its_limit_of_teams(session: Session) -> None:
    for team in (TEAM, OTHER):
        materials.register(session, team, title="링크", link="b" * 20)

    assert materials.forget_memberless_team_materials(session, limit=1) == 1
    assert held(session, TEAM) == NOTHING
    assert held(session, OTHER)["materials"] == 1


def test_the_task_goes_on_until_no_such_team_is_left(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add_all([Team(id=f"team_{n}", name="빈 팀") for n in range(3, 6)])
    session.add(TeamMember(team_id=OTHER, user_id="someone_else"))
    for team in (TEAM, OTHER, "team_3", "team_4", "team_5"):
        materials.register(session, team, title="링크", link="c" * 20)
    session.commit()
    scopes: list[int] = []

    @contextmanager
    def scope() -> Iterator[Session]:
        scopes.append(1)
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(materials, "MEMBERLESS_BATCH", 2)

    assert tasks.forget_memberless_team_materials() == 4
    assert len(scopes) == 3  # 2, 2, then 0 < the batch: done
    assert set(session.scalars(select(ExtMaterial.team_id))) == {OTHER}


def test_the_task_takes_no_argument() -> None:
    assert tasks.forget_memberless_team_materials.name == (
        "autune.extraction.periodic.forget_memberless_team_materials"
    )
    with pytest.raises(TypeError):
        tasks.forget_memberless_team_materials("anything")  # type: ignore[call-arg]
