"""A text the team already keeps is not kept a second time (#817).

Seen through the app: a first upload went unanswered -- the proxy gave up
while the server was still working -- the screen said to upload again, and the
member who did had the file twice. Nothing refused the second copy, and the two
took two of a search's five places with one text.

An upload whose masked pieces the team already keeps now answers that earlier
row and writes nothing. These tests go through ``store_upload`` and the route;
two uploads at once are in ``integration/test_materials_same_text_pg.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
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
from autune_extraction import materials
from autune_extraction.config import get_settings
from autune_extraction.models import ExtMaterial, ExtMaterialAlarm, ExtMaterialChunk
from autune_extraction.router import router

from .conftest import sign_in

TEAM = "team_1"
OTHER_TEAM = "team_2"
PREFIX = "/api/extraction"
UPLOAD = f"{PREFIX}/materials/upload?team_id={TEAM}"

PHONE = "010-2345-6789"
BODY = f"출장비 한도\n\n국내 출장 숙박비는 1박 12만원까지다.\n문의는 총무 담당 {PHONE}.\n"
LONG = "".join(f"{n}번째 줄: 회의록은 진행자가 당일 안에 올린다.\n" for n in range(60))
"""More than one piece (``CHUNK_CHARS`` is 500)."""


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    owned = {
        "ext_materials",
        "ext_material_chunks",
        "ext_material_alarms",
        "ext_material_settings",
    }
    tables = [t for name, t in Base.metadata.tables.items() if name in shared | owned]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s


def upload(
    session: Session,
    *,
    team: str = TEAM,
    title: str = "출장비 한도",
    name: str = "limits.txt",
    body: str = BODY,
) -> materials.StoredUpload:
    return materials.store_upload(session, team, title=title, file_name=name, data=body.encode())


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def a_second_copy(session: Session, monkeypatch: pytest.MonkeyPatch, **given: str) -> ExtMaterial:
    """A copy as a team may hold from before this change: stored without the
    look for an earlier one."""
    with monkeypatch.context() as patched:
        patched.setattr(materials, "_kept_already", lambda *_, **__: None)
        return upload(session, **given).row


# --- the same text ----------------------------------------------------------------


def test_the_same_text_again_answers_the_first_row_and_writes_nothing(session: Session) -> None:
    first = upload(session).row
    kept = (first.id, first.title, first.created_at, first.expires_at)
    pieces = count(session, ExtMaterialChunk)

    again = upload(session, title="다시 올린 제목")

    assert again.already_kept
    assert again.row.id == first.id
    assert (first.id, first.title, first.created_at, first.expires_at) == kept
    assert count(session, ExtMaterial) == 1
    assert count(session, ExtMaterialChunk) == pieces
    assert not session.new and not session.dirty


def test_a_new_text_is_kept_and_says_so(session: Session) -> None:
    first = upload(session)
    other = upload(session, title="회의 규칙", body=LONG)

    assert not first.already_kept and not other.already_kept
    assert other.row.id != first.row.id
    assert count(session, ExtMaterial) == 2


def test_nothing_is_embedded_for_a_text_already_kept(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry costs a read and a mask, not a run of the model."""
    upload(session)
    runs: list[int] = []
    real = materials.embedded

    def counted(embedder: object, texts: list[str]) -> list[list[float]]:
        runs.append(len(texts))
        return real(embedder, texts)  # type: ignore[arg-type]

    monkeypatch.setattr(materials, "embedded", counted)

    assert upload(session).already_kept
    assert runs == []


def test_the_text_is_compared_and_not_the_file(session: Session) -> None:
    """The file is not kept, so there is nothing of it to compare: the same
    text under another name and ending is the same material."""
    first = upload(session, name="limits.txt").row

    again = upload(session, name="출장비_한도_최종.md")

    assert again.already_kept and again.row.id == first.id


@pytest.mark.parametrize(
    "changed",
    [BODY + " ", BODY.replace("12만원", "13만원"), " " + BODY, BODY.rstrip("\n")],
    ids=["a space after", "one figure", "a space before", "no last line break"],
)
def test_a_text_that_differs_by_a_character_is_another_material(
    session: Session, changed: str
) -> None:
    upload(session)

    other = upload(session, body=changed)

    assert not other.already_kept
    assert count(session, ExtMaterial) == 2


def test_texts_that_differ_only_under_the_mask_are_one_material(session: Session) -> None:
    """What is compared is what is stored. Two phone numbers of one shape are
    the same stars, and a second copy would be the first's, character for
    character."""
    first = upload(session).row

    again = upload(session, body=BODY.replace(PHONE, "010-9876-5432"))

    assert again.already_kept and again.row.id == first.id
    assert PHONE not in "".join(materials.material_text(session, first.id))


def test_every_piece_is_compared_not_only_the_first(session: Session) -> None:
    first = upload(session, body=LONG).row
    assert len(materials.material_text(session, first.id)) > 2
    last_line_changed = LONG.replace("59번째 줄: 회의록은", "59번째 줄: 안건은")
    assert last_line_changed != LONG

    longer = upload(session, body=LONG + "덧붙인 줄.\n")
    changed = upload(session, body=last_line_changed)
    shorter = upload(session, body=materials.material_text(session, first.id)[0])

    assert not longer.already_kept and not changed.already_kept and not shorter.already_kept
    assert count(session, ExtMaterial) == 4
    assert upload(session, body=LONG).row.id == first.id


# --- whose, and which -------------------------------------------------------------


def test_another_teams_copy_is_not_this_teams(session: Session) -> None:
    """An upload reads its own team's rows only: what another team keeps
    changes nothing in the answer, so an upload cannot be used to ask."""
    theirs = upload(session, team=OTHER_TEAM).row

    ours = upload(session)

    assert not ours.already_kept
    assert ours.row.id != theirs.id
    assert (ours.row.team_id, theirs.team_id) == (TEAM, OTHER_TEAM)
    assert count(session, ExtMaterial) == 2
    assert upload(session).row.id == ours.row.id
    assert upload(session, team=OTHER_TEAM).row.id == theirs.id


def test_a_copy_past_its_day_is_not_kept_unless_the_team_keeps_until_deleted(
    session: Session,
) -> None:
    """The hourly task is about to delete it: answering "it is kept" would
    leave the member with nothing once it has run. The search's own rule."""
    past = upload(session).row
    past.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()

    fresh = upload(session)

    assert not fresh.already_kept and fresh.row.id != past.id

    materials.delete_material(session, TEAM, fresh.row.id)
    materials.set_keep_until_deleted(session, TEAM, True)

    assert upload(session).row.id == past.id


def test_a_deleted_copy_is_not_kept(session: Session) -> None:
    first = upload(session).row
    materials.delete_material(session, TEAM, first.id)

    again = upload(session)

    assert not again.already_kept
    assert count(session, ExtMaterial) == 1


def test_a_copy_another_model_embedded_is_not_kept(session: Session) -> None:
    """No search reads it under the model in use; the member's upload would
    be answered "kept" and never found."""
    old = upload(session).row
    old.embedding_model = "a-model-no-longer-in-use"
    session.flush()

    again = upload(session)

    assert not again.already_kept and again.row.id != old.id


def test_a_copy_with_a_piece_no_search_can_read_is_not_kept(session: Session) -> None:
    old = upload(session, body=LONG).row
    last = session.scalars(
        select(ExtMaterialChunk)
        .where(ExtMaterialChunk.material_id == old.id)
        .order_by(ExtMaterialChunk.position.desc())
    ).first()
    assert last is not None and last.position > 0
    last.embedding = None
    session.flush()

    again = upload(session, body=LONG)

    assert not again.already_kept and again.row.id != old.id


def test_of_several_copies_the_one_kept_longest_answers(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = upload(session).row
    second = a_second_copy(session, monkeypatch, title="둘째")
    third = a_second_copy(session, monkeypatch, title="셋째")
    day = datetime.now(UTC) + timedelta(days=30)
    first.expires_at, second.expires_at, third.expires_at = (
        day,
        day + timedelta(days=20),
        day + timedelta(days=10),
    )
    session.flush()

    assert upload(session).row.id == second.id
    assert count(session, ExtMaterial) == 3


# --- the cap, the marking, the title ----------------------------------------------


def test_a_text_already_kept_is_answered_on_a_full_shelf_and_takes_no_place(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materials, "MAX_MATERIALS", 2)
    first = upload(session).row

    assert upload(session).row.id == first.id
    assert upload(session).row.id == first.id
    upload(session, title="회의 규칙", body=LONG)
    assert count(session, ExtMaterial) == 2

    assert upload(session).row.id == first.id
    with pytest.raises(AutuneError) as refused:
        upload(session, title="셋째", body=BODY + "덧붙인 줄.\n")
    assert refused.value.details == {"field": "file", "reason": "shelf_full"}
    assert count(session, ExtMaterial) == 2


def test_a_marked_file_is_refused_though_its_text_is_kept(session: Session) -> None:
    """The marking is looked for before anything the team keeps is read: the
    refusal and its alarm row are what they were."""
    upload(session)

    with pytest.raises(materials.ConfidentialFileError):
        upload(session, name="confidential_limits.txt")

    assert count(session, ExtMaterialAlarm) == 1
    assert count(session, ExtMaterial) == 1


def test_a_title_is_still_screened_though_it_would_not_be_stored(session: Session) -> None:
    upload(session)

    with pytest.raises(AutuneError) as refused:
        upload(session, title=f"담당 {PHONE}")

    assert refused.value.status_code == 422
    assert count(session, ExtMaterial) == 1


def test_the_log_line_carries_two_ids_and_nothing_a_member_sent(session: Session) -> None:
    first = upload(session).row

    with capture_logs() as logs:
        upload(session, title="다시 올린 제목", name="출장비_한도_최종.md")

    assert logs == [
        {
            "event": "extraction_material_upload_already_kept",
            "log_level": "info",
            "material_id": first.id,
            "team_id": TEAM,
        }
    ]


# --- the route --------------------------------------------------------------------


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


def post(client: TestClient, *, title: str, name: str = "limits.txt", body: str = BODY):  # type: ignore[no-untyped-def]
    return client.post(
        UPLOAD, data={"title": title}, files={"file": (name, body.encode(), "text/plain")}
    )


def without_created_at(body: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in body.items() if key != "created_at"}


def moment(written: str) -> datetime:
    return datetime.fromisoformat(written).replace(tzinfo=None)


def test_the_route_answers_201_for_a_new_text_and_200_with_the_first_row_for_one_kept(
    client: TestClient, session: Session
) -> None:
    first = post(client, title="출장비 한도")
    again = post(client, title="다시 올린 제목")

    assert (first.status_code, first.json()["already_kept"]) == (201, False)
    assert (again.status_code, again.json()["already_kept"]) == (200, True)
    # The earlier row as it is: its id, its title, its days. Not the title sent.
    # (``created_at`` aside: SQLite hands it back without its zone.)
    assert without_created_at(again.json()) == without_created_at(
        {**first.json(), "already_kept": True}
    )
    assert moment(again.json()["created_at"]) == moment(first.json()["created_at"])
    assert again.json()["title"] == "출장비 한도"
    assert "다시 올린 제목" not in again.text

    listed = client.get(f"{PREFIX}/materials?team_id={TEAM}").json()
    assert [(m["id"], m["title"], m["already_kept"]) for m in listed] == [
        (first.json()["id"], "출장비 한도", False)
    ]
    assert count(session, ExtMaterial) == 1
