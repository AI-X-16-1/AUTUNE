"""How an upload is cut, and what is embedded with a piece (#817, ADR 0011).

The rules under test:

- the pieces of a text join back into it, whatever the headings and the size,
  so the same text cut the same way is the same pieces;
- a piece is at most the size, cut at the coarsest separator that lets it fit;
- a heading is a markdown ``#`` line or a short numbered line (``2.``,
  ``2.1``, ``2.1.1``), read from the masked text, and the path is the headings
  above a piece, outermost first;
- the path is in the text that is embedded and in no stored column: what is
  stored and shown is the piece alone;
- the tail of the piece before it, within a section, is in the embedded text
  too, and in no stored column; pieces and boundaries are the same with it
  on or off;
- the embedded string passes the outbound guard like the piece does.

Every value here is invented.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Team, TeamMember, User
from autune_extraction import material_search, materials
from autune_extraction.config import get_settings
from autune_extraction.material_pieces import (
    MAX_PATH_CHARS,
    Piece,
    heading_of,
    pieces_of,
    sections,
    split_recursive,
)
from autune_extraction.models import MATERIAL_EMBEDDING_DIM, ExtMaterialChunk
from autune_extraction.pipeline import FakeEmbedder

TEAM = "team_1"

REPORT = (
    "1. 지사별 현황\n"
    "1.1 해운대 지사\n"
    "이탈률은 3.2%로 집계됐고 직전 분기에는 4.1%였다.\n"
    "1.2 서면 지사\n"
    "이탈률은 5.0%로 집계됐고 직전 분기에는 4.8%였다.\n"
    "2. 계획\n"
    "다음 분기 점검은 2월 3일에 한다.\n"
)


# --- headings ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# 제목", (1, "제목")),
        ("### 세 번째 단계 ", (3, "세 번째 단계")),
        ("2. 계획", (1, "2. 계획")),
        ("2.1 해운대 지사", (2, "2.1 해운대 지사")),
        ("2.1.1 지표", (3, "2.1.1 지표")),
        ("  3.2.1.   공급사   ", (3, "3.2.1.   공급사")),
    ],
)
def test_a_markdown_line_or_a_short_numbered_line_is_a_heading(
    line: str, expected: tuple[int, str]
) -> None:
    assert heading_of(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "#태그는 제목이 아니다",
        "2024 매출 100",
        "100 명이 참석했다",
        "1.2.3.4 네 단계는 제목이 아니다",
        "1. " + "아주 긴 목록의 문장 " * 6,
        "평범한 문장이다.",
        "",
    ],
)
def test_other_lines_are_not_headings(line: str) -> None:
    assert heading_of(line) is None


# --- sections and paths -----------------------------------------------------------


def test_the_sections_join_back_into_the_text() -> None:
    assert "".join(body for _, body in sections(REPORT)) == REPORT


def test_a_path_is_the_headings_above_outermost_first_and_a_level_closes_its_deeper_ones() -> None:
    paths = {body.splitlines()[0]: path for path, body in sections(REPORT)}

    assert paths["1. 지사별 현황"] == "1. 지사별 현황"
    assert paths["1.1 해운대 지사"] == "1. 지사별 현황 > 1.1 해운대 지사"
    assert paths["1.2 서면 지사"] == "1. 지사별 현황 > 1.2 서면 지사"
    assert paths["2. 계획"] == "2. 계획"


def test_text_before_the_first_heading_has_no_path() -> None:
    [(path, body)] = sections("머리말 한 줄\n")

    assert (path, body) == ("", "머리말 한 줄\n")


def test_a_long_path_loses_its_outermost_headings_first() -> None:
    text = "# " + "가" * 70 + "\n## " + "나" * 70 + "\n### 가장 가까운 제목\n본문입니다.\n"

    [*_, (path, _)] = sections(text)

    assert len(path) <= MAX_PATH_CHARS
    assert path.endswith("가장 가까운 제목")


# --- pieces -----------------------------------------------------------------------


@pytest.mark.parametrize("size", [40, 80, 200, 500])
def test_the_pieces_join_back_into_the_text_with_or_without_the_path(size: int) -> None:
    text = REPORT * 6 + "x" * 300

    for with_path in (True, False):
        pieces = pieces_of(text, size, with_path=with_path)

        assert "".join(piece.text for piece in pieces) == text
        assert all(0 < len(piece.text) <= size for piece in pieces)


def test_a_piece_is_cut_at_the_coarsest_separator_that_lets_it_fit() -> None:
    text = "첫 문단의 문장입니다.\n\n둘째 문단의 문장입니다. 둘째 문단의 둘째 문장입니다.\n"

    pieces = split_recursive(text, 25)

    assert pieces[0] == "첫 문단의 문장입니다.\n\n"
    assert pieces[1].endswith(". ")
    assert "".join(pieces) == text


def test_a_word_longer_than_the_size_is_cut_at_the_size() -> None:
    assert split_recursive("x" * 250, 100) == ["x" * 100, "x" * 100, "x" * 50]


def test_a_blank_run_joins_the_piece_before_it_and_is_not_a_piece_of_its_own() -> None:
    text = "가" * 40 + "\n\n\n\n" + "나" * 40

    pieces = pieces_of(text, 40)

    assert [piece.text.strip() for piece in pieces] == ["가" * 40, "나" * 40]
    assert "".join(piece.text for piece in pieces) == text


def test_the_embedded_text_is_the_path_then_the_piece_and_the_piece_alone_without_one() -> None:
    [first, *_] = pieces_of(REPORT, 200)
    with_path = Piece("이탈률은 3.2%로 집계됐다.", "1.1 해운대 지사")

    assert first.path == "1. 지사별 현황"
    assert with_path.embedded == "1.1 해운대 지사\n이탈률은 3.2%로 집계됐다."
    assert Piece("본문", "").embedded == "본문"


def test_the_boundaries_are_the_same_with_the_path_on_or_off_and_in_cut() -> None:
    text = REPORT * 4

    on = [piece.text for piece in pieces_of(text, 60, with_path=True)]
    off = [piece.text for piece in pieces_of(text, 60, with_path=False)]

    assert on == off == materials.cut(text, 60)


def test_the_path_can_be_turned_off() -> None:
    assert all(piece.path == "" for piece in pieces_of(REPORT, 200, with_path=False))


def test_a_piece_carries_the_tail_of_the_piece_before_it_in_its_section() -> None:
    pieces = pieces_of(REPORT, 30, overlap=8)

    assert any(piece.lead for piece in pieces)
    for before, piece in zip(pieces, pieces[1:], strict=False):
        if piece.lead:
            assert piece.lead == before.text[-8:]
            assert piece.embedded.endswith(piece.lead + piece.text)
            assert piece.path == before.path


def test_the_lead_never_crosses_a_heading_line() -> None:
    pieces = pieces_of(REPORT, 30, overlap=8)
    opens_a_section = [
        piece for piece in pieces[1:] if heading_of(piece.text.split("\n")[0]) is not None
    ]

    assert opens_a_section
    assert all(piece.lead == "" for piece in opens_a_section)
    assert pieces[0].lead == ""


def test_the_overlap_moves_nothing_that_is_stored_or_compared() -> None:
    text = REPORT * 4 + "x" * 130

    plain = pieces_of(text, 60)
    over = pieces_of(text, 60, overlap=20)

    assert [piece.text for piece in over] == [piece.text for piece in plain]
    assert [piece.path for piece in over] == [piece.path for piece in plain]
    assert "".join(piece.text for piece in over) == text
    assert all(piece.lead == "" for piece in plain)
    assert [piece.text for piece in over] == materials.cut(text, 60)


def test_a_lead_is_only_ever_a_slice_of_the_text_that_runs_into_the_piece() -> None:
    text = REPORT * 3
    pieces = pieces_of(text, 45, overlap=15)
    start = 0

    for piece in pieces:
        assert text[start : start + len(piece.text)] == piece.text
        if piece.lead:
            assert text[start - len(piece.lead) : start] == piece.lead
        start += len(piece.text)


def test_a_blank_tail_is_not_a_lead() -> None:
    pieces = pieces_of("가" * 20 + "\n\n" + "나" * 20, 22, overlap=2)

    assert [piece.text for piece in pieces] == ["가" * 20 + "\n\n", "나" * 20]
    assert pieces[1].lead == ""


# --- the upload -------------------------------------------------------------------


class Recording(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(dim=MATERIAL_EMBEDDING_DIM)
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return super().embed(texts)


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> Recording:
    fake = Recording()
    monkeypatch.setattr(materials, "get_material_embedder", lambda: fake)
    monkeypatch.setattr(material_search, "get_material_embedder", lambda: fake)
    monkeypatch.setattr(get_settings(), "material_search_min_similarity", -1.0)
    monkeypatch.setattr(get_settings(), "material_search_max_below_best", 2.0)
    monkeypatch.setattr(get_settings(), "material_search_word_match_min_similarity", 2.0)
    return fake


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


def store(session: Session, body: str, title: str = "보고서"):
    return materials.store_upload(
        session, TEAM, title=title, file_name="report.md", data=body.encode()
    ).row


def test_the_path_is_embedded_and_the_stored_piece_is_the_piece_alone(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_chunk_chars", 80)
    row = store(session, REPORT)

    stored = list(
        session.scalars(
            select(ExtMaterialChunk.text)
            .where(ExtMaterialChunk.material_id == row.id)
            .order_by(ExtMaterialChunk.position)
        )
    )

    assert "".join(stored) == REPORT
    assert not any(" > " in text for text in stored)
    assert any(seen.startswith("1. 지사별 현황 > 1.1 해운대 지사\n") for seen in embedder.seen)
    assert len(embedder.seen) == len(stored)


def test_without_the_path_what_is_embedded_is_the_piece(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_chunk_chars", 80)
    monkeypatch.setattr(get_settings(), "material_heading_path", False)
    monkeypatch.setattr(get_settings(), "material_embed_overlap_chars", 0)
    row = store(session, REPORT)

    stored = list(
        session.scalars(
            select(ExtMaterialChunk.text)
            .where(ExtMaterialChunk.material_id == row.id)
            .order_by(ExtMaterialChunk.position)
        )
    )

    assert embedder.seen == stored


def test_the_tail_of_the_piece_before_is_embedded_and_never_stored(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_chunk_chars", 30)
    monkeypatch.setattr(get_settings(), "material_embed_overlap_chars", 20)
    row = store(session, REPORT)

    stored = list(
        session.scalars(
            select(ExtMaterialChunk.text)
            .where(ExtMaterialChunk.material_id == row.id)
            .order_by(ExtMaterialChunk.position)
        )
    )

    assert "".join(stored) == REPORT
    assert stored == [piece.text for piece in pieces_of(REPORT, 30)]
    # Exactly what the cutter says is embedded -- the path, the tail, the piece --
    # and not what the path alone would give: a heading line at the end of the
    # piece before is also the end of the next piece's path.
    with_tail = [piece.embedded for piece in pieces_of(REPORT, 30, overlap=20)]
    without = [piece.embedded for piece in pieces_of(REPORT, 30)]
    assert embedder.seen == with_tail
    assert with_tail != without
    assert [piece.text for piece in pieces_of(REPORT, 30, overlap=20)] == stored


def test_the_same_text_is_one_material_with_the_overlap_on(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_chunk_chars", 80)
    monkeypatch.setattr(get_settings(), "material_embed_overlap_chars", 20)
    first = materials.store_upload(
        session, TEAM, title="보고서", file_name="a.md", data=REPORT.encode()
    )
    second = materials.store_upload(
        session, TEAM, title="다시", file_name="b.md", data=REPORT.encode()
    )

    assert second.already_kept is True
    assert second.row.id == first.row.id


@pytest.mark.parametrize("heading_path", [True, False])
def test_a_lead_the_guard_refuses_writes_nothing(
    session: Session,
    embedder: Recording,
    monkeypatch: pytest.MonkeyPatch,
    heading_path: bool,
) -> None:
    """With the path off a piece has a lead and no path: the string it makes
    with the lead is still looked at, since a pattern across the cut between
    two pieces is only seen in that string."""
    from autune_core.errors import PrivacyViolationError

    monkeypatch.setattr(get_settings(), "material_chunk_chars", 30)
    monkeypatch.setattr(get_settings(), "material_embed_overlap_chars", 20)
    monkeypatch.setattr(get_settings(), "material_heading_path", heading_path)
    real = materials.assert_masked

    def refuse_embedded(text: str, *, destination: str) -> None:
        if destination == "material_embedder":
            raise PrivacyViolationError("refused")
        real(text, destination=destination)

    monkeypatch.setattr(materials, "assert_masked", refuse_embedded)

    with pytest.raises(PrivacyViolationError):
        materials.store_upload(
            session, TEAM, title="보고서", file_name="a.md", data=REPORT.encode()
        )

    assert session.scalars(select(ExtMaterialChunk)).all() == []
    assert embedder.seen == []


def test_the_same_text_is_one_material_with_the_path_on(
    session: Session, embedder: Recording
) -> None:
    first = materials.store_upload(
        session, TEAM, title="보고서", file_name="a.md", data=REPORT.encode()
    )
    second = materials.store_upload(
        session, TEAM, title="다시", file_name="b.md", data=REPORT.encode()
    )

    assert second.already_kept is True
    assert second.row.id == first.row.id


# --- several excerpts -------------------------------------------------------------


def test_a_hit_carries_one_excerpt_unless_the_setting_asks_for_more(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_chunk_chars", 80)
    store(session, REPORT)

    [hit] = material_search.search_materials(session, TEAM, "해운대 지사 이탈률").hits

    assert hit.more_excerpts == ()


def test_a_hit_carries_the_next_nearest_pieces_when_the_setting_allows(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_chunk_chars", 80)
    monkeypatch.setattr(get_settings(), "material_search_excerpts", 3)
    store(session, REPORT)

    [hit] = material_search.search_materials(session, TEAM, "해운대 지사 이탈률").hits

    assert len(hit.more_excerpts) == 2
    assert hit.excerpt not in hit.more_excerpts
    assert len(set(hit.more_excerpts)) == 2
    assert all(len(text) <= material_search.EXCERPT_CHARS + 2 for text in hit.more_excerpts)


def test_an_extra_excerpt_the_screen_refuses_is_left_out_and_the_hit_stays(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autune_core.errors import PrivacyViolationError

    monkeypatch.setattr(get_settings(), "material_chunk_chars", 80)
    monkeypatch.setattr(get_settings(), "material_search_excerpts", 3)
    store(session, REPORT)
    real = material_search.screen_output
    calls: list[str] = []

    def screen(text: str) -> str:
        calls.append(text)
        if len(calls) > 2:  # the title and the hit's own excerpt pass; extras refused
            raise PrivacyViolationError("refused")
        return real(text)

    monkeypatch.setattr(material_search, "screen_output", screen)

    answer = material_search.search_materials(session, TEAM, "해운대 지사 이탈률")

    [hit] = answer.hits
    assert hit.more_excerpts == ()
