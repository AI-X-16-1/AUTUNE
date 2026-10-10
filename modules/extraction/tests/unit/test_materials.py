"""A team's materials: Drive files kept on the 자료 screen (#817).

The rules under test: only a Google Drive file link is taken, and what is kept
of it is the file's id and its kind -- never the pasted text; a title is
tidied and bounded; a file a team already keeps is refused, and so is one past
the cap; only the team's members list, register and delete; nothing a row or a
log line holds names a person, a title or a file id.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError
from autune_extraction import materials
from autune_extraction.materials import DriveFile, parse_drive_link
from autune_extraction.models import ExtMaterial
from autune_extraction.router import router

from .conftest import sign_in

TEAM = "team_1"
PREFIX = "/api/extraction"
FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
DOC_ID = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654"


# --- the link ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        (f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing", DriveFile(FILE_ID, "file")),
        (f"https://drive.google.com/open?id={FILE_ID}", DriveFile(FILE_ID, "file")),
        (f"https://drive.google.com/uc?id={FILE_ID}&export=download", DriveFile(FILE_ID, "file")),
        (
            f"https://docs.google.com/document/d/{DOC_ID}/edit#heading=h.1",
            DriveFile(DOC_ID, "document"),
        ),
        (
            f"https://docs.google.com/presentation/d/{DOC_ID}/edit",
            DriveFile(DOC_ID, "presentation"),
        ),
        (
            f"https://docs.google.com/spreadsheets/d/{DOC_ID}/edit#gid=0",
            DriveFile(DOC_ID, "spreadsheets"),
        ),
        (f"  {FILE_ID}\n", DriveFile(FILE_ID, "file")),
    ],
)
def test_a_drive_file_link_gives_the_id_and_the_kind(pasted: str, expected: DriveFile) -> None:
    assert parse_drive_link(pasted) == expected


@pytest.mark.parametrize(
    "pasted",
    [
        "",
        "회의 자료",
        f"http://drive.google.com/file/d/{FILE_ID}/view",  # not https
        f"https://example.com/file/d/{FILE_ID}/view",  # another host
        f"https://drive.google.com.example.com/file/d/{FILE_ID}/view",
        f"https://drive.google.com@example.com/file/d/{FILE_ID}/view",  # the host is example.com
        f"https://drive.google.com/drive/folders/{FILE_ID}",  # a folder
        "https://drive.google.com/file/d/short/view",  # not an id
        "https://drive.google.com/file/d/",
        "https://drive.google.com/open",
        f"https://docs.google.com/forms/d/{DOC_ID}/edit",  # not an editor with a preview
        f"https://docs.google.com/document/u/0/{DOC_ID}",
        f"javascript:alert('{FILE_ID}')",
        "https://[not-a-host/file/d/x",
    ],
)
def test_anything_else_names_no_file(pasted: str) -> None:
    assert parse_drive_link(pasted) is None


# --- the shelf --------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    tables = [
        t
        for name, t in Base.metadata.tables.items()
        if name in shared or name in ("ext_materials", "ext_material_chunks")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s


def link(file_id: str = FILE_ID) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"


def test_what_is_kept_is_the_id_and_the_kind_not_the_pasted_text(session: Session) -> None:
    pasted = f"https://docs.google.com/document/d/{DOC_ID}/edit?tab=t.0#heading=h.abc"

    row = materials.register(session, TEAM, title="  3분기   로드맵 \n", link=pasted)

    assert (row.title, row.drive_file_id, row.drive_kind) == ("3분기 로드맵", DOC_ID, "document")
    stored = {c.name: getattr(row, c.name) for c in ExtMaterial.__table__.columns}
    assert not any("docs.google.com" in str(v) or "heading" in str(v) for v in stored.values())


def test_a_row_names_no_person() -> None:
    assert {c.name for c in ExtMaterial.__table__.columns} == {
        "id",
        "team_id",
        "title",
        "source",
        "drive_file_id",
        "drive_kind",
        "created_at",
    }


@pytest.mark.parametrize("title", ["", "   ", "가" * 121])
def test_a_blank_or_overlong_title_is_refused(session: Session, title: str) -> None:
    with pytest.raises(AutuneError) as refused:
        materials.register(session, TEAM, title=title, link=link())
    assert refused.value.status_code == 422


def test_a_title_of_the_longest_length_is_kept(session: Session) -> None:
    assert materials.register(session, TEAM, title="가" * 120, link=link()).title == "가" * 120


def test_a_refused_link_is_not_in_the_error(session: Session) -> None:
    pasted = "https://example.com/secret-path?token=abc123"
    with pytest.raises(AutuneError) as refused:
        materials.register(session, TEAM, title="자료", link=pasted)
    assert refused.value.status_code == 422
    assert "example.com" not in str(refused.value) and "abc123" not in str(refused.value.to_dict())


def test_a_file_the_team_already_keeps_is_refused_and_another_teams_is_not(
    session: Session,
) -> None:
    materials.register(session, TEAM, title="기획서", link=link())
    # The same file by another address is the same file.
    with pytest.raises(AutuneError) as refused:
        materials.register(
            session, TEAM, title="같은 파일", link=f"https://drive.google.com/open?id={FILE_ID}"
        )
    assert refused.value.status_code == 409

    materials.register(session, "team_2", title="기획서", link=link())

    assert [m.title for m in materials.team_materials(session, TEAM)] == ["기획서"]
    assert [m.title for m in materials.team_materials(session, "team_2")] == ["기획서"]


def test_a_team_keeps_at_most_the_cap(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materials, "MAX_MATERIALS", 2)
    materials.register(session, TEAM, title="하나", link=link("a" * 20))
    materials.register(session, TEAM, title="둘", link=link("b" * 20))

    with pytest.raises(AutuneError) as refused:
        materials.register(session, TEAM, title="셋", link=link("c" * 20))

    assert refused.value.status_code == 422
    # Another team's shelf is its own.
    materials.register(session, "team_2", title="셋", link=link("c" * 20))


def test_the_log_carries_ids_and_neither_the_title_nor_the_file(session: Session) -> None:
    with capture_logs() as lines:
        row = materials.register(session, TEAM, title="김민수 평가 자료", link=link())
        materials.delete_material(session, TEAM, row.id)

    assert [line["event"] for line in lines] == [
        "extraction_material_registered",
        "extraction_material_deleted",
    ]
    said = repr(lines)
    assert "김민수" not in said and FILE_ID not in said
    assert all(line["material_id"] == row.id and line["team_id"] == TEAM for line in lines)


def test_deleting_another_teams_material_finds_nothing(session: Session) -> None:
    theirs = materials.register(session, "team_2", title="남의 자료", link=link())

    with pytest.raises(AutuneError) as refused:
        materials.delete_material(session, TEAM, theirs.id)

    assert refused.value.status_code == 404
    assert session.get(ExtMaterial, theirs.id) is not None


# --- the routes -------------------------------------------------------------------


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


def test_a_member_registers_lists_and_deletes(client: TestClient) -> None:
    first = client.post(
        f"{PREFIX}/materials?team_id={TEAM}", json={"title": "기획서", "link": link()}
    )
    second = client.post(
        f"{PREFIX}/materials?team_id={TEAM}",
        json={"title": "예산", "link": f"https://docs.google.com/spreadsheets/d/{DOC_ID}/edit"},
    )
    assert (first.status_code, second.status_code) == (201, 201)
    body = second.json()
    assert set(body) == {
        "id",
        "team_id",
        "title",
        "source",
        "drive_file_id",
        "drive_kind",
        "created_at",
        "expires_at",
        "not_read",
    }
    assert (body["source"], body["drive_file_id"], body["drive_kind"]) == (
        "drive_link",
        DOC_ID,
        "spreadsheets",
    )
    assert (body["expires_at"], body["not_read"]) == (None, [])
    assert body["id"].startswith("mat_")

    listed = client.get(f"{PREFIX}/materials?team_id={TEAM}").json()
    assert {m["title"] for m in listed} == {"기획서", "예산"}

    assert client.delete(f"{PREFIX}/materials/{body['id']}?team_id={TEAM}").status_code == 204
    assert [m["title"] for m in client.get(f"{PREFIX}/materials?team_id={TEAM}").json()] == [
        "기획서"
    ]
    assert client.delete(f"{PREFIX}/materials/{body['id']}?team_id={TEAM}").status_code == 404


def test_the_routes_refuse_what_is_not_a_drive_link_and_a_file_twice(client: TestClient) -> None:
    url = f"{PREFIX}/materials?team_id={TEAM}"
    assert (
        client.post(url, json={"title": "자료", "link": "https://example.com/a"}).status_code == 422
    )
    assert client.post(url, json={"title": " ", "link": link()}).status_code == 422
    assert client.post(url, json={"title": "자료", "link": link(), "extra": 1}).status_code == 422
    assert client.post(url, json={"title": "자료", "link": link()}).status_code == 201
    assert client.post(url, json={"title": "다시", "link": link()}).status_code == 409


def test_another_teams_shelf_is_not_there(client: TestClient, session: Session) -> None:
    theirs = materials.register(session, "team_2", title="남의 자료", link=link())
    session.commit()

    assert client.get(f"{PREFIX}/materials?team_id=team_2").status_code == 404
    assert (
        client.post(
            f"{PREFIX}/materials?team_id=team_2", json={"title": "X", "link": link("z" * 20)}
        ).status_code
        == 404
    )
    assert client.delete(f"{PREFIX}/materials/{theirs.id}?team_id=team_2").status_code == 404
    # Named under the caller's own team, another team's row is still not found.
    assert client.delete(f"{PREFIX}/materials/{theirs.id}?team_id={TEAM}").status_code == 404
    assert session.scalar(select(ExtMaterial.id).where(ExtMaterial.id == theirs.id)) == theirs.id


def test_a_member_who_left_the_team_reads_and_writes_nothing(
    client: TestClient, session: Session
) -> None:
    kept = client.post(
        f"{PREFIX}/materials?team_id={TEAM}", json={"title": "기획서", "link": link()}
    ).json()
    for membership in session.scalars(select(TeamMember).where(TeamMember.team_id == TEAM)):
        session.delete(membership)
    session.flush()

    assert client.get(f"{PREFIX}/materials?team_id={TEAM}").status_code == 404
    assert client.delete(f"{PREFIX}/materials/{kept['id']}?team_id={TEAM}").status_code == 404
    # What they registered stays with the team.
    assert session.get(ExtMaterial, kept["id"]) is not None
