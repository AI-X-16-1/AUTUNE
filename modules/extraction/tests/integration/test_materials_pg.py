"""The search over a meeting's materials, on PostgreSQL with ``pgvector``.

``materials.search`` is one query -- cosine distance by the extension's own
operator, under the index the migration builds -- so it is tested where that
query runs. The embedder here puts each text on an axis named by a word in it,
so which chunk is nearest a question is known without a model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_extraction import materials
from autune_extraction.models import EMBEDDING_DIM, ExtMaterial, ExtMaterialChunk

AXES = ["결제", "예산", "출시", "채용"]


class Axes:
    """A vector with weight on the axis of each known word a text holds."""

    model_version = "axes-test"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(text.count(word)) for word in AXES] + [0.01] + [0.0] * (EMBEDDING_DIM - 5)
            for text in texts
        ]


class AsIs:
    def mask(self, text: str) -> str:
        return text


def world(session: Session) -> None:
    session.add_all([Team(id="team_m1", name="하나"), Team(id="team_m2", name="둘")])
    session.flush()
    session.add_all(
        [
            Meeting(id="mtg_m1", team_id="team_m1", title="주간 회의"),
            Meeting(id="mtg_m2", team_id="team_m1", title="기획 회의"),
            Meeting(
                id="mtg_m_old",
                team_id="team_m1",
                title="지난 회의",
                expires_at=datetime.now(tz=UTC) - timedelta(days=1),
            ),
            Meeting(id="mtg_m_other", team_id="team_m2", title="다른 팀 회의"),
        ]
    )
    session.flush()


def keep(session: Session, meeting_id: str, title: str, text: str) -> ExtMaterial:
    if meeting_id == "mtg_m_old":
        # Registered while the meeting was live; it has expired since.
        material = ExtMaterial(
            meeting_id=meeting_id,
            title=title,
            source="upload",
            model_version="axes-test",
            chunks=[ExtMaterialChunk(position=0, text=text, embedding=Axes().embed([text])[0])],
        )
        session.add(material)
        session.flush()
        return material
    kept = materials.register(
        session,
        meeting_id=meeting_id,
        title=title,
        source="upload",
        text=text,
        masker=AsIs(),
        embedder=Axes(),
    )
    assert kept is not None
    return kept


def test_a_question_finds_the_chunk_that_is_about_it_nearest_first(db_session: Session) -> None:
    world(db_session)
    keep(db_session, "mtg_m1", "결제 개편안", "결제 화면의 결제 버튼을 위로 올립니다.")
    keep(db_session, "mtg_m1", "예산안", "예산은 팀장 승인 뒤 확정합니다.")
    keep(db_session, "mtg_m2", "출시 계획", "출시는 다음 달 첫째 주입니다.")

    hits = materials.ask(db_session, team_id="team_m1", question="예산 승인", embedder=Axes())

    assert [hit.title for hit in hits][0] == "예산안"
    assert hits[0].text == "예산은 팀장 승인 뒤 확정합니다." and hits[0].position == 0
    assert hits[0].distance < 0.1 < hits[1].distance
    assert [hit.distance for hit in hits] == sorted(hit.distance for hit in hits)


def test_only_the_teams_own_live_meetings_are_read(db_session: Session) -> None:
    """The visibility a meeting's own rows have: the team's, and within retention."""
    world(db_session)
    keep(db_session, "mtg_m1", "우리 채용안", "채용 계획입니다.")
    keep(db_session, "mtg_m_old", "지난 채용안", "채용 채용 채용 계획입니다.")
    keep(db_session, "mtg_m_other", "다른 팀 채용안", "채용 채용 계획입니다.")

    hits = materials.ask(db_session, team_id="team_m1", question="채용", embedder=Axes())

    assert [hit.title for hit in hits] == ["우리 채용안"]
    other = materials.ask(db_session, team_id="team_m2", question="채용", embedder=Axes())
    assert [hit.title for hit in other] == ["다른 팀 채용안"]


def test_a_search_can_be_kept_to_one_meeting_and_returns_five_at_most(
    db_session: Session,
) -> None:
    world(db_session)
    for n in range(7):
        keep(db_session, "mtg_m1", f"출시 자료 {n}", f"출시 일정 {n}번 안입니다.")
    keep(db_session, "mtg_m2", "기획 회의의 출시 자료", "출시 출시 일정입니다.")

    everywhere = materials.ask(db_session, team_id="team_m1", question="출시", embedder=Axes())
    one = materials.ask(
        db_session, team_id="team_m1", question="출시", embedder=Axes(), meeting_id="mtg_m2"
    )
    asked_for_more = materials.ask(
        db_session, team_id="team_m1", question="출시", embedder=Axes(), limit=50
    )

    assert len(everywhere) == materials.MAX_HITS == len(asked_for_more)
    assert [hit.title for hit in one] == ["기획 회의의 출시 자료"]
    assert materials.ask(db_session, team_id="team_m1", question="  ", embedder=Axes()) == []


def test_a_material_goes_with_its_meeting(db_session: Session) -> None:
    world(db_session)
    keep(db_session, "mtg_m1", "결제 개편안", "결제 화면입니다.")
    db_session.flush()

    db_session.execute(sa.delete(Meeting).where(Meeting.id == "mtg_m1"))
    db_session.expire_all()

    assert db_session.query(ExtMaterial).count() == 0
    assert db_session.query(ExtMaterialChunk).count() == 0


def test_the_search_is_the_extensions_and_has_its_index(db_session: Session) -> None:
    indexes = db_session.execute(
        sa.text("select indexdef from pg_indexes where tablename = 'ext_material_chunks'")
    ).scalars()

    assert any("hnsw" in d and "vector_cosine_ops" in d for d in indexes)
