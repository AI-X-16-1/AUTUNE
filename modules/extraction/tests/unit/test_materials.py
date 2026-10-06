"""A meeting's material: what is stored, and what the write refuses.

The search itself is a ``pgvector`` query and is tested on PostgreSQL
(``tests/integration/test_materials_pg.py``). Here: the chunking, and the write
through ``materials.register`` on SQLite -- the masker is required, what it
leaves unmasked is refused, and only masked text is kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import (
    Base,
    Meeting,
    Participant,
    PrivacyViolationError,
    TeamMember,
    User,
    Utterance,
)
from autune_extraction import materials, tools
from autune_extraction.models import EMBEDDING_DIM, ExtMaterial, ExtMaterialChunk
from autune_extraction.pipeline import FakeEmbedder

MEETING = "mtg_1"
PHONE = "010-1234-5678"
BODY = (
    "3분기 결제 화면 개편 계획입니다. 첫 화면의 버튼 위치를 위로 올립니다. "
    "예산은 팀장 승인 뒤 확정합니다. 출시는 다음 달 첫째 주를 목표로 합니다."
)


class Masker:
    """Hides the one phone number these tests use, the way a document masker
    hides what it knows."""

    def mask(self, text: str) -> str:
        return text.replace(PHONE, "[전화번호]")


class Blind:
    """A masker that misses it."""

    def mask(self, text: str) -> str:
        return text


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
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(
            Meeting(
                id="mtg_old",
                team_id="team_1",
                title="지난 회의",
                expires_at=datetime.now(tz=UTC) - timedelta(days=1),
            )
        )
        s.commit()
        yield s


def keep(session: Session, text: str = BODY, **more: object) -> ExtMaterial | None:
    fields: dict = {
        "meeting_id": MEETING,
        "title": "결제 화면 개편안",
        "source": "upload",
        "text": text,
        "masker": Masker(),
        "embedder": FakeEmbedder(),
    }
    fields.update(more)
    return materials.register(session, **fields)


# --- chunks -------------------------------------------------------------------------


def test_chunks_keep_sentences_whole_and_fit_the_limit() -> None:
    text = " ".join(
        f"{n}번째 문장은 자료의 한 줄이고 길이는 대략 서른 자쯤 됩니다." for n in range(60)
    )

    got = materials.chunks(text)

    assert len(got) > 1
    assert all(len(chunk) <= materials.CHUNK_CHARS for chunk in got)
    assert all(chunk.endswith("됩니다.") for chunk in got)  # never cut mid-sentence
    # Each chunk after the first opens with the line the one before it closed on.
    assert all(
        later.startswith(earlier.rsplit(". ", 1)[-1])
        for earlier, later in zip(got, got[1:], strict=False)
    )


def test_a_sentence_longer_than_a_chunk_is_cut_at_a_space_and_nothing_is_lost() -> None:
    words = [f"낱말{n}" for n in range(400)]

    got = materials.chunks(" ".join(words))

    assert all(len(chunk) <= materials.CHUNK_CHARS for chunk in got)
    assert " ".join(got).split() == words


def test_blank_text_is_no_chunk() -> None:
    assert materials.chunks("  \n\n ") == []


# --- the write ------------------------------------------------------------------------


def test_what_is_kept_is_the_masked_text_in_order_with_a_vector_a_chunk(
    session: Session,
) -> None:
    with capture_logs() as logs:
        material = keep(session, f"{BODY} 문의는 {PHONE}으로 주세요.", source_ref="file_1")
    session.commit()

    assert material is not None and material.id.startswith("mat_")
    assert material.model_version == "fake" and material.source_ref == "file_1"
    rows = session.query(ExtMaterialChunk).order_by(ExtMaterialChunk.position).all()
    assert [row.position for row in rows] == list(range(len(rows)))
    stored = " ".join(row.text for row in rows)
    assert "[전화번호]" in stored and PHONE not in stored
    assert all(len(row.embedding) == EMBEDDING_DIM for row in rows)
    # Ids and counts in the log; nothing of the document.
    (entry,) = [e for e in logs if e["event"] == "extraction_material_registered"]
    assert entry["chunks"] == len(rows)
    assert "결제" not in repr(logs) and PHONE not in repr(logs)


def test_a_value_the_masker_missed_refuses_the_document_and_nothing_is_stored(
    session: Session,
) -> None:
    with pytest.raises(PrivacyViolationError) as refused:
        keep(session, f"{BODY} 문의는 {PHONE}으로 주세요.", masker=Blind())

    assert PHONE not in str(refused.value) and PHONE not in repr(refused.value.to_dict())
    assert session.query(ExtMaterial).count() == 0
    assert session.query(ExtMaterialChunk).count() == 0


def test_an_unmasked_title_refuses_it_too(session: Session) -> None:
    with pytest.raises(PrivacyViolationError):
        keep(session, title=f"담당 {PHONE}", masker=Blind())

    assert session.query(ExtMaterial).count() == 0


def test_there_is_no_way_to_register_without_a_masker(session: Session) -> None:
    with pytest.raises(TypeError):
        materials.register(  # type: ignore[call-arg]
            session,
            meeting_id=MEETING,
            title="제목",
            source="upload",
            text=BODY,
            embedder=FakeEmbedder(),
        )


def test_the_same_source_registered_again_replaces_what_was_kept(session: Session) -> None:
    keep(session, "처음 내용입니다.", source="drive", source_ref="file_1")
    keep(session, "다른 자료입니다.", source="drive", source_ref="file_2")
    keep(session, "고쳐 쓴 내용입니다.", source="drive", source_ref="file_1")
    session.commit()

    kept = {m.source_ref: [c.text for c in m.chunks] for m in session.query(ExtMaterial)}
    assert kept == {"file_1": ["고쳐 쓴 내용입니다."], "file_2": ["다른 자료입니다."]}
    assert session.query(ExtMaterialChunk).count() == 2


def test_a_meeting_past_retention_or_unknown_takes_no_material(session: Session) -> None:
    assert keep(session, meeting_id="mtg_old") is None
    assert keep(session, meeting_id="mtg_none") is None
    assert session.query(ExtMaterial).count() == 0


def test_an_unknown_source_is_a_bug_not_a_document(session: Session) -> None:
    with pytest.raises(ValueError, match="unknown source"):
        keep(session, source="notion")


def test_a_wider_vector_than_the_column_is_refused(session: Session) -> None:
    class Wide(FakeEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * (EMBEDDING_DIM + 1) for _ in texts]

    with pytest.raises(ValueError, match="dimensions"):
        keep(session, embedder=Wide())


def test_removing_a_material_takes_its_chunks(session: Session) -> None:
    material = keep(session)
    assert material is not None
    session.commit()

    assert materials.remove(session, material.id) is True
    assert materials.remove(session, material.id) is False
    assert session.query(ExtMaterialChunk).count() == 0


# --- the tool ---------------------------------------------------------------------------


def test_the_tool_lists_titles_and_counts_and_no_text(session: Session) -> None:
    keep(session, source="drive", source_ref="file_1")
    session.commit()

    answer = tools.meeting_materials(session, MEETING)

    assert answer["ok"] is True and answer["summary"] == "이 회의에 등록된 자료 1건."
    (item,) = answer["items"]
    assert item["title"] == "결제 화면 개편안" and item["body"].startswith("Drive, 조각 ")
    assert "버튼" not in repr(answer)  # a title and a count; the text stays in the table
    assert answer["evidence"] == []


def test_the_tool_says_so_for_a_meeting_it_cannot_read(session: Session) -> None:
    assert tools.meeting_materials(session, "mtg_old")["ok"] is False
    assert tools.meeting_materials(session, MEETING)["items"] == []
    assert tools.meeting_materials in tools.TOOLS
