"""How long a team keeps an uploaded material (the owner, 2026-10-11).

- By default an upload keeps the team's retention window, counted from the
  upload (``teams.retention_days``), and the hourly task deletes it after,
  with its text.
- A team may switch to keeping uploads until a member deletes them; switching
  back gives every existing upload its window again, counted from its own
  upload.
- A Drive link keeps no text and has no window.
- One team's setting is never another's; the setting records no person.
- Any member reads and switches it; nobody else.

Time is passed in (``now=``), and an upload's ``created_at`` is set, so
nothing here waits on the clock. Every value is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError
from autune_extraction import materials, tasks
from autune_extraction.config import get_settings
from autune_extraction.models import ExtMaterial, ExtMaterialChunk, ExtMaterialSetting
from autune_extraction.router import router

from .conftest import sign_in

TEAM = "team_1"
OTHER = "team_2"
PREFIX = "/api/extraction"
SETTINGS = f"{PREFIX}/materials/settings?team_id={TEAM}"
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
BODY = "3분기 계획\n예산 1200만원\n" * 60


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    owned = {"ext_materials", "ext_material_chunks", "ext_material_settings"}
    tables = [t for name, t in Base.metadata.tables.items() if name in shared | owned]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add_all([Team(id=TEAM, name="팀", retention_days=90), Team(id=OTHER, name="남의 팀")])
        s.flush()
        yield s


def upload(session: Session, team: str = TEAM, *, age: timedelta, title: str = "계획"):
    row = materials.store_upload(
        session, team, title=title, file_name="plan.txt", data=BODY.encode()
    ).row
    row.created_at = NOW - age
    session.flush()
    return row


def link(session: Session, team: str = TEAM, *, age: timedelta, file: str = "a"):
    row = materials.register(session, team, title="링크", link=file * 20)
    row.created_at = NOW - age
    session.flush()
    return row


def ids(session: Session) -> set[str]:
    return set(session.scalars(select(ExtMaterial.id)))


def chunks(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ExtMaterialChunk)) or 0


# --- the window --------------------------------------------------------------------


def test_by_default_an_upload_expires_its_teams_window_after_the_upload(
    session: Session,
) -> None:
    row = upload(session, age=timedelta(days=10))

    window = materials.upload_window(session, TEAM)

    assert window == timedelta(days=90)
    assert materials.read(row, window).expires_at == NOW + timedelta(days=80)


def test_the_window_is_the_teams_own_retention_days(session: Session) -> None:
    session.get(Team, TEAM).retention_days = 30  # type: ignore[union-attr]
    row = upload(session, age=timedelta(days=0))

    assert materials.read(row, materials.upload_window(session, TEAM)).expires_at == (
        NOW + timedelta(days=30)
    )


def test_an_upload_past_its_window_is_deleted_with_its_text(session: Session) -> None:
    old = upload(session, age=timedelta(days=90, seconds=1), title="오래된")
    young = upload(session, age=timedelta(days=89), title="새")

    assert materials.forget_expired_uploads(session, now=NOW) == 1

    assert ids(session) == {young.id}
    assert materials.material_text(session, old.id) == []
    assert materials.material_text(session, young.id) != []


def test_keep_until_deleted_keeps_every_upload_and_answers_no_day(session: Session) -> None:
    row = upload(session, age=timedelta(days=400))
    materials.set_keep_until_deleted(session, TEAM, True)

    assert materials.upload_window(session, TEAM) is None
    assert materials.read(row, None).expires_at is None
    assert materials.forget_expired_uploads(session, now=NOW) == 0
    assert ids(session) == {row.id}


def test_switching_back_makes_old_uploads_expire_from_their_upload(session: Session) -> None:
    materials.set_keep_until_deleted(session, TEAM, True)
    old = upload(session, age=timedelta(days=120), title="오래된")
    young = upload(session, age=timedelta(days=5), title="새")
    assert materials.forget_expired_uploads(session, now=NOW) == 0

    materials.set_keep_until_deleted(session, TEAM, False)

    window = materials.upload_window(session, TEAM)
    assert materials.read(young, window).expires_at == NOW + timedelta(days=85)
    assert materials.forget_expired_uploads(session, now=NOW) == 1
    assert ids(session) == {young.id}
    assert materials.material_text(session, old.id) == []


def test_a_link_never_expires(session: Session) -> None:
    row = link(session, age=timedelta(days=1000))

    assert materials.read(row, materials.upload_window(session, TEAM)).expires_at is None
    assert materials.forget_expired_uploads(session, now=NOW) == 0
    assert ids(session) == {row.id}


def test_one_teams_setting_and_window_are_its_own(session: Session) -> None:
    session.get(Team, OTHER).retention_days = 10  # type: ignore[union-attr]
    ours = upload(session, TEAM, age=timedelta(days=50))
    theirs = upload(session, OTHER, age=timedelta(days=50))
    materials.set_keep_until_deleted(session, OTHER, True)

    assert materials.keeps_until_deleted(session, TEAM) is False
    assert materials.forget_expired_uploads(session, now=NOW) == 0
    assert ids(session) == {ours.id, theirs.id}

    materials.set_keep_until_deleted(session, OTHER, False)
    assert materials.forget_expired_uploads(session, now=NOW) == 1
    assert ids(session) == {ours.id}


def test_a_batch_deletes_at_most_its_limit_the_oldest_first(session: Session) -> None:
    rows = [upload(session, age=timedelta(days=100 + n), title=f"자료 {n}") for n in range(5)]

    assert materials.forget_expired_uploads(session, now=NOW, limit=2) == 2
    assert ids(session) == {row.id for row in rows[:3]}
    assert materials.forget_expired_uploads(session, now=NOW, limit=2) == 2
    assert materials.forget_expired_uploads(session, now=NOW, limit=2) == 1
    assert ids(session) == set()
    assert chunks(session) == 0


def test_the_setting_records_no_person(session: Session) -> None:
    assert {c.name for c in ExtMaterialSetting.__table__.columns} == {
        "team_id",
        "keep_until_deleted",
        "updated_at",
    }


def test_switching_twice_is_one_row_per_team(session: Session) -> None:
    materials.set_keep_until_deleted(session, TEAM, True)
    materials.set_keep_until_deleted(session, TEAM, False)
    materials.set_keep_until_deleted(session, TEAM, True)

    assert session.scalar(select(func.count()).select_from(ExtMaterialSetting)) == 1
    assert materials.keeps_until_deleted(session, TEAM) is True


def test_expiry_logs_ids_and_nothing_a_member_sent(session: Session) -> None:
    row = upload(session, age=timedelta(days=91), title="김민수 평가")

    with capture_logs() as lines:
        materials.forget_expired_uploads(session, now=NOW)

    assert lines == [
        {
            "event": "extraction_material_expired",
            "log_level": "info",
            "material_id": row.id,
            "team_id": TEAM,
        }
    ]


# --- the task ----------------------------------------------------------------------


def test_the_task_deletes_in_batches_until_nothing_is_left(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(5):
        upload(session, age=timedelta(days=200 + n), title=f"자료 {n}")
    kept = upload(session, age=timedelta(days=1), title="새")
    session.commit()
    scopes: list[int] = []

    @contextmanager
    def scope() -> Iterator[Session]:
        scopes.append(1)
        yield session
        session.commit()

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
            return NOW

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "datetime", Frozen)
    monkeypatch.setattr(materials, "FORGET_BATCH", 2)

    assert tasks.forget_expired_material_uploads() == 5
    assert len(scopes) == 3  # 2, 2, then 1 < the batch: done
    assert ids(session) == {kept.id}
    assert chunks(session) == len(materials.material_text(session, kept.id))


def test_the_task_takes_no_argument() -> None:
    assert tasks.forget_expired_material_uploads.name == (
        "autune.extraction.periodic.forget_expired_material_uploads"
    )
    with pytest.raises(TypeError):
        tasks.forget_expired_material_uploads("anything")  # type: ignore[call-arg]


# --- the routes ---------------------------------------------------------------------


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


def test_a_member_reads_and_switches_the_setting(client: TestClient, session: Session) -> None:
    assert client.get(SETTINGS).json() == {"keep_until_deleted": False, "retention_days": 90}

    answer = client.put(SETTINGS, json={"keep_until_deleted": True})

    assert answer.status_code == 200
    assert answer.json() == {"keep_until_deleted": True, "retention_days": 90}
    assert client.get(SETTINGS).json()["keep_until_deleted"] is True
    assert materials.keeps_until_deleted(session, OTHER) is False


def test_someone_not_on_the_team_neither_reads_nor_switches(
    client: TestClient, session: Session
) -> None:
    theirs = f"{PREFIX}/materials/settings?team_id={OTHER}"

    assert client.get(theirs).status_code == 404
    assert client.put(theirs, json={"keep_until_deleted": True}).status_code == 404
    assert materials.keeps_until_deleted(session, OTHER) is False


def test_the_list_answers_each_rows_day(client: TestClient, session: Session) -> None:
    row = upload(session, age=timedelta(days=10))
    drive = link(session, age=timedelta(days=10))
    session.commit()

    listed = {m["id"]: m["expires_at"] for m in client.get(
        f"{PREFIX}/materials?team_id={TEAM}").json()}  # fmt: skip
    assert datetime.fromisoformat(listed[row.id]).replace(tzinfo=UTC) == NOW + timedelta(days=80)
    assert listed[drive.id] is None

    client.put(SETTINGS, json={"keep_until_deleted": True})
    listed = {m["id"]: m["expires_at"] for m in client.get(
        f"{PREFIX}/materials?team_id={TEAM}").json()}  # fmt: skip
    assert listed == {row.id: None, drive.id: None}


def test_an_upload_answers_its_day_or_none(client: TestClient) -> None:
    def post() -> dict[str, object]:
        return client.post(
            f"{PREFIX}/materials/upload?team_id={TEAM}",
            data={"title": "계획"},
            files={"file": ("plan.txt", BODY.encode(), "text/plain")},
        ).json()

    first = post()
    created = datetime.fromisoformat(str(first["created_at"]))
    assert datetime.fromisoformat(str(first["expires_at"])) - created == timedelta(days=90)

    client.put(SETTINGS, json={"keep_until_deleted": True})
    assert post()["expires_at"] is None
