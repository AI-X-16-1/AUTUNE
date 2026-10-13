"""A material found by the question's words, on PostgreSQL (#817).

What the unit suite cannot show on SQLite: that the two statements a word
match adds read the asking team's live uploads and no other row; that they
carry no word of the question; and that the pieces under the floor are read
without the vector index, which answers only the nearest few.

Every value here is invented.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from autune_core import Team
from autune_extraction import material_search, materials
from autune_extraction.config import get_settings
from autune_extraction.material_search import search_materials
from autune_extraction.models import ExtMaterial, ExtMaterialChunk

RETURNS = (
    "이번 분기 물류 운영에서 바꾸는 것을 적는다.\n"
    "야간 반품 접수 처리에 걸리는 시간이 평균 이틀이라, 창구를 한 곳으로 모은다.\n"
    "교대 근무표는 주 단위로 정한다.\n"
)
ROOMS = "회의실 예약은 사내 캘린더에서 한다. 한 번에 두 시간까지 잡는다.\n"
SHORT = "야간 반품 접수"
NOTHING_PASSES = 0.999


@pytest.fixture(autouse=True)
def only_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake embedder's scores are not on the real one's scale: nothing
    passes the floor here, so every hit is one the words found."""
    monkeypatch.setattr(get_settings(), "material_search_min_similarity", NOTHING_PASSES)
    monkeypatch.setattr(get_settings(), "material_search_max_below_best", 2.0)
    monkeypatch.setattr(get_settings(), "material_search_word_match_min_similarity", -1.0)


def new_team(session: Session, name: str) -> str:
    team = Team(name=name)
    session.add(team)
    session.flush()
    return team.id


@pytest.fixture
def team_id(db_session: Session) -> str:
    return new_team(db_session, "팀")


def upload(session: Session, team_id: str, body: str, title: str) -> str:
    return materials.store_upload(
        session, team_id, title=title, file_name="plan.txt", data=body.encode()
    ).row.id


def both(session: Session, team_id: str) -> tuple[str, str]:
    """One upload that holds ``SHORT`` in its title, one in its body."""
    return (
        upload(session, team_id, ROOMS, "야간 반품 접수 안내"),
        upload(session, team_id, RETURNS, "물류 운영 메모"),
    )


def ids(session: Session, team_id: str, **kwargs: object) -> list[str]:
    return [h.material_id for h in search_materials(session, team_id, SHORT, **kwargs).hits]  # type: ignore[arg-type]


def test_a_title_and_a_piece_under_the_floor_are_found_and_no_other_teams(
    db_session: Session, team_id: str
) -> None:
    other = new_team(db_session, "다른 팀")
    both(db_session, other)
    titled, body = both(db_session, team_id)
    upload(db_session, team_id, ROOMS + "덧붙임.\n", "예약")

    answer = search_materials(db_session, team_id, SHORT)

    assert [hit.material_id for hit in answer.hits] == [titled, body]
    assert answer.more is False and "야간 반품 접수" in answer.hits[1].excerpt
    assert all(hit.score < NOTHING_PASSES for hit in answer.hits)


def test_the_word_match_off_finds_neither(
    db_session: Session, team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    both(db_session, team_id)
    monkeypatch.setattr(get_settings(), "material_search_word_match_min_similarity", 2.0)

    assert ids(db_session, team_id) == []


def test_an_upload_is_not_found_by_a_word_from_the_moment_of_its_day(
    db_session: Session, team_id: str
) -> None:
    titled, body = both(db_session, team_id)
    days = sorted(
        db_session.scalars(sa.select(ExtMaterial.expires_at).where(ExtMaterial.team_id == team_id))
    )
    assert all(isinstance(day, datetime) for day in days)

    assert set(ids(db_session, team_id, now=days[0] - timedelta(seconds=1))) == {titled, body}
    assert ids(db_session, team_id, now=days[-1]) == []

    materials.set_keep_until_deleted(db_session, team_id, True)
    assert set(ids(db_session, team_id, now=days[-1] + timedelta(days=400))) == {titled, body}


def test_a_piece_of_another_model_and_a_deleted_material_are_not_found_by_a_word(
    db_session: Session, team_id: str
) -> None:
    titled, body = both(db_session, team_id)
    db_session.execute(
        sa.update(ExtMaterial).where(ExtMaterial.id == titled).values(embedding_model="another")
    )
    assert ids(db_session, team_id) == [body]

    materials.delete_material(db_session, team_id, body)
    assert ids(db_session, team_id) == []


def test_no_statement_of_a_search_carries_a_word_of_the_question(
    db_session: Session, team_id: str
) -> None:
    """A bound parameter is part of a failed statement's message, and the
    engine does not hide parameters (#356)."""
    both(db_session, team_id)
    sent: list[str] = []

    def record(
        conn: Connection, cursor: object, statement: str, parameters: object, *_: object
    ) -> None:
        sent.append(f"{statement} {parameters!r}")

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        found = ids(db_session, team_id)
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert len(found) == 2
    # The vectors, each material's nearest piece, the pieces under the floor
    # -- and the team's choice of how long it keeps its uploads.
    assert sum("ext_material_chunks" in line for line in sent) == 3
    assert not any(word in line for line in sent for word in ("야간", "반품", "접수"))


def test_the_pieces_under_the_floor_are_read_without_the_vector_index(
    db_session: Session, team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index answers the nearest few (``hnsw.ef_search``, 40 by default)
    whatever the limit says. Pushed onto the index, the planner still reads
    every piece of the band, because the band is not ordered by the operator
    the index is built on."""
    pieces = 60
    for n in range(pieces):
        upload(db_session, team_id, f"{ROOMS}{n}번째 방.\n", f"방 {n}")
    held = upload(db_session, team_id, RETURNS * 6, "물류 운영 메모")
    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtMaterialChunk)
            .join(ExtMaterial, ExtMaterial.id == ExtMaterialChunk.material_id)
            .where(ExtMaterial.team_id == team_id)
        )
        > pieces
    )
    db_session.execute(sa.text("SET LOCAL enable_seqscan = off"))
    db_session.execute(sa.text("SET LOCAL enable_sort = off"))
    read: list[int] = []
    under = material_search._under_the_floor

    def counted(*args: object, **kwargs: object) -> list[material_search._Piece]:
        rows = under(*args, **kwargs)  # type: ignore[arg-type]
        read.append(len(rows))
        return rows

    monkeypatch.setattr(material_search, "_under_the_floor", counted)

    found = ids(db_session, team_id)

    assert found == [held]
    assert read and read[0] > pieces
