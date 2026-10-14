"""A material is also found by the question's words (#817, 2026-10-13).

The floor loses short questions and questions that repeat a file's title:
their vectors are not near enough to the piece that answers. So a material is
also a hit when every word of the masked question is in its title, or in a
piece of it that scores under the floor and at or over the word match's own
lower bound. The rules under test (``material_search``'s docstring):

- the words reach only what the vectors reach: the asking team's uploads,
  not past their day, of this embedder -- never a Drive link;
- the words are the masked question's, are compared in this process and are
  in no statement, log line or result;
- a title or an excerpt found by words is screened like any other, and a
  refused one is dropped;
- the materials over the floor are exactly those the floor and the cut alone
  leave; title matches come before them and piece matches after;
- it is a substring match.

The fake embedder counts shared characters, so each test reads the scores it
gets and sets the floor and the bound around them. Every value is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Team, TeamMember, User, get_session
from autune_core.errors import PrivacyViolationError
from autune_extraction import material_search, materials
from autune_extraction.config import ExtractionSettings, get_settings
from autune_extraction.material_search import (
    NOTHING_CLOSE,
    QUESTION_MASKED,
    WORD_CANDIDATES,
    search_materials,
)
from autune_extraction.models import MATERIAL_EMBEDDING_DIM, ExtMaterial, ExtMaterialChunk
from autune_extraction.pipeline import FakeEmbedder
from autune_extraction.router import router

from .conftest import sign_in

TEAM = "team_1"
OTHER = "team_2"
PHONE = "010-2345-6789"
RETURNS = (
    "이번 분기 물류 운영에서 바꾸는 것을 적는다.\n"
    "야간 반품 접수 처리에 걸리는 시간이 평균 이틀이라, 창구를 한 곳으로 모은다.\n"
    "교대 근무표는 주 단위로 정한다.\n"
)
PIPES = "노후 배관 교체 공사에 드는 비용은 층마다 따로 견적을 받는다.\n"
ROOMS = "회의실 예약은 사내 캘린더에서 한다. 한 번에 두 시간까지 잡는다.\n"
WHOLE = "야간 반품 접수 처리에"
SHORT = "야간 반품 접수"
"""``WHOLE`` without its last word: the same three words, further from the
piece that holds them."""

OFF = 2.0
NO_FLOOR = -1.0
NOTHING_PASSES = 0.999


@pytest.fixture(autouse=True)
def scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """No floor, no cut and no lower bound for a word match until a test
    sets one: the fake's scores are not on the real embedder's scale."""
    rule(monkeypatch, floor=NO_FLOOR, cut=2.0, words=NO_FLOOR)


def rule(
    monkeypatch: pytest.MonkeyPatch,
    *,
    floor: float | None = None,
    cut: float | None = None,
    words: float | None = None,
) -> None:
    for name, value in (
        ("material_search_min_similarity", floor),
        ("material_search_max_below_best", cut),
        ("material_search_word_match_min_similarity", words),
    ):
        if value is not None:
            monkeypatch.setattr(get_settings(), name, value)


@pytest.fixture(autouse=True)
def embedder(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    fake = FakeEmbedder(dim=MATERIAL_EMBEDDING_DIM)
    monkeypatch.setattr(materials, "get_material_embedder", lambda: fake)
    monkeypatch.setattr(material_search, "get_material_embedder", lambda: fake)
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


def upload(session: Session, body: str, *, team: str = TEAM, title: str = "자료") -> ExtMaterial:
    return materials.store_upload(
        session, team, title=title, file_name="plan.txt", data=body.encode()
    ).row


def ids(session: Session, question: str, **kwargs: object) -> list[str]:
    return [hit.material_id for hit in search_materials(session, TEAM, question, **kwargs).hits]  # type: ignore[arg-type]


def score_of(session: Session, question: str, row: ExtMaterial) -> float:
    """What ``row`` scores for ``question``, read with nothing switched on."""
    with pytest.MonkeyPatch.context() as patch:
        rule(patch, floor=NO_FLOOR, cut=2.0, words=OFF)
        scores = {
            hit.material_id: hit.score for hit in search_materials(session, TEAM, question).hits
        }
    return scores[row.id]


# --- what it is for -----------------------------------------------------------------


def test_a_body_phrase_without_its_last_word_is_found_through_the_word_match(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The phrase is over the floor; its first three words are under it, and
    every one of them is in the piece."""
    memo = upload(session, RETURNS, title="물류 운영 메모")
    whole, short = score_of(session, WHOLE, memo), score_of(session, SHORT, memo)
    assert whole > short
    rule(monkeypatch, floor=(whole + short) / 2, cut=0.05, words=OFF)
    assert ids(session, WHOLE) == [memo.id]
    assert ids(session, SHORT) == []

    rule(monkeypatch, words=short - 0.01)

    answer = search_materials(session, TEAM, SHORT)
    [hit] = answer.hits
    assert hit.material_id == memo.id and "야간 반품 접수" in hit.excerpt
    assert hit.score == round(short, 4) and answer.notice is None
    assert ids(session, WHOLE) == [memo.id]


def test_a_question_that_repeats_the_title_finds_the_file_whatever_its_pieces_score(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The body says it in other words; the title holds the question's."""
    named = upload(session, ROOMS, title="설비 교체 견적 문서")
    upload(session, PIPES, title="점검")
    rule(monkeypatch, floor=NOTHING_PASSES, words=NOTHING_PASSES)

    assert ids(session, "설비 교체 견적") == [named.id]
    assert ids(session, "설비 교체 견적 문서") == [named.id]
    assert ids(session, "설비 교체 일정") == []


def test_every_word_has_to_be_there(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    upload(session, RETURNS, title="물류 운영 메모")
    rule(monkeypatch, floor=NOTHING_PASSES)

    assert ids(session, "야간 반품") != []
    assert ids(session, "야간 반품 환불") == []
    assert ids(session, "물류 환불") == []


def test_a_word_in_the_title_and_another_in_the_body_is_no_match(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(session, RETURNS, title="점검 기록")
    rule(monkeypatch, floor=NOTHING_PASSES)

    assert ids(session, "점검 반품") == []


def test_it_is_a_substring_match(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Korean glues endings and other nouns to a word: 롤백 is found in
    롤백은, and 지사 in 부산지사 -- which is not the word that was asked."""
    row = upload(session, "부산지사 이전은 내년으로 미룬다.\n", title="롤백은 누가")
    rule(monkeypatch, floor=NOTHING_PASSES)

    assert ids(session, "지사") == [row.id]
    assert ids(session, "롤백") == [row.id]
    assert ids(session, "ROLLBACK") == []
    row.title = "Rollback 절차"
    session.flush()
    assert ids(session, "ROLLBACK") == [row.id]


def test_a_title_that_is_a_file_name_is_matched_through_its_marks(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Underscores, hyphens, brackets and an ending part the words of a name
    as spaces do; so do they in the question."""
    row = upload(session, ROOMS, title="3분기_물류-운영 계획(최종).PDF")
    rule(monkeypatch, floor=NOTHING_PASSES, words=NOTHING_PASSES)

    assert ids(session, "3분기 물류 운영") == [row.id]
    assert ids(session, "물류_운영 계획.pdf") == [row.id]
    assert ids(session, "최종 계획") == [row.id]
    assert ids(session, "4분기 물류") == []


@pytest.mark.parametrize("question", ["앱", "가 나 다", "- / ?"])
def test_a_question_with_no_word_of_two_letters_gets_no_word_match(
    session: Session, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    upload(session, "앱 가 나 다\n", title="앱 가 나 다")
    rule(monkeypatch, floor=NOTHING_PASSES)

    answer = search_materials(session, TEAM, question)

    assert answer.hits == [] and answer.notice == NOTHING_CLOSE


# --- the setting ----------------------------------------------------------------------


def test_the_bound_has_a_default_for_the_real_embedder_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    field = ExtractionSettings.model_fields["material_search_word_match_min_similarity"]
    assert field.default == 0.3

    name = "AUTUNE_EXTRACTION_MATERIAL_SEARCH_WORD_MATCH_MIN_SIMILARITY"
    monkeypatch.setenv(name, "0.35")
    assert ExtractionSettings().material_search_word_match_min_similarity == 0.35
    for refused in ("-1.5", "2.5"):
        monkeypatch.setenv(name, refused)
        with pytest.raises(PydanticValidationError):
            ExtractionSettings()


def test_a_piece_under_the_bound_is_not_found_by_its_words_and_a_title_still_is(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = upload(session, RETURNS, title="물류 운영 메모")
    titled = upload(session, ROOMS, title="야간 반품 접수 안내")
    under = score_of(session, SHORT, body)
    rule(monkeypatch, floor=NOTHING_PASSES, words=under + 0.01)
    assert ids(session, SHORT) == [titled.id]

    rule(monkeypatch, words=under)
    assert ids(session, SHORT) == [titled.id, body.id]


def test_two_turns_the_word_match_off_the_titles_too(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(session, RETURNS, title="야간 반품 접수 안내")
    rule(monkeypatch, floor=NOTHING_PASSES, words=1.0)
    assert ids(session, SHORT) != []

    rule(monkeypatch, words=OFF)

    answer = search_materials(session, TEAM, SHORT)
    assert answer.hits == [] and answer.notice == NOTHING_CLOSE


# --- 1. the words reach what the vectors reach -----------------------------------------


def day_of(row: ExtMaterial) -> datetime:
    day = materials.expires_at(row, kept=False)
    assert day is not None
    return day


def both(session: Session, *, team: str = TEAM) -> tuple[ExtMaterial, ExtMaterial]:
    """One upload that holds ``SHORT`` in its title, one in its body."""
    return (
        upload(session, ROOMS, team=team, title="야간 반품 접수 안내"),
        upload(session, RETURNS, team=team, title="물류 운영 메모"),
    )


def test_another_teams_upload_is_not_found_by_a_word(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(Team(id=OTHER, name="다른 팀"))
    both(session, team=OTHER)
    ours = upload(session, PIPES, title="점검")
    rule(monkeypatch, floor=NOTHING_PASSES)

    answer = search_materials(session, TEAM, SHORT)

    assert answer.hits == [] and answer.more is False
    assert ids(session, "배관 교체") == [ours.id]


def test_an_upload_past_its_day_is_not_found_by_a_word(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    titled, body = both(session)
    rule(monkeypatch, floor=NOTHING_PASSES)
    first, last = sorted(day_of(row) for row in (titled, body))

    assert set(ids(session, SHORT, now=first - timedelta(seconds=1))) == {titled.id, body.id}
    answer = search_materials(session, TEAM, SHORT, now=last)
    assert answer.hits == [] and answer.more is False


def test_a_team_that_keeps_its_uploads_finds_one_past_its_day_by_a_word(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    titled, body = both(session)
    rule(monkeypatch, floor=NOTHING_PASSES)
    late = day_of(body) + timedelta(days=400)
    assert ids(session, SHORT, now=late) == []

    materials.set_keep_until_deleted(session, TEAM, True)

    assert set(ids(session, SHORT, now=late)) == {titled.id, body.id}


def test_a_drive_link_and_a_piece_of_another_model_are_not_found_by_a_word(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materials.register(session, TEAM, title="야간 반품 접수 링크", link="a" * 20)
    for row in both(session):
        row.embedding_model = "another-model"
    session.flush()
    rule(monkeypatch, floor=NOTHING_PASSES)

    answer = search_materials(session, TEAM, SHORT)

    assert answer.hits == [] and answer.more is False


def test_a_deleted_material_is_not_found_by_a_word(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    titled, body = both(session)
    rule(monkeypatch, floor=NOTHING_PASSES)
    assert len(ids(session, SHORT)) == 2

    materials.delete_material(session, TEAM, titled.id)
    materials.delete_material(session, TEAM, body.id)

    assert ids(session, SHORT) == []


# --- 2. the masked question's words, and nowhere but here ---------------------------------


def test_a_value_the_masker_hid_in_the_question_is_no_word(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored piece and a title that still hold the number (a detector that
    learned the shape since) are not found by asking for the number: what is
    matched is the masked question, and stars are no word."""
    row = upload(session, RETURNS, title="물류 운영 메모")
    chunk = session.scalars(select(ExtMaterialChunk)).one()
    chunk.text = f"야간 반품 담당 {PHONE}\n"
    row.title = "담당 010 2345 6789"
    session.flush()
    rule(monkeypatch, floor=NOTHING_PASSES)

    answer = search_materials(session, TEAM, PHONE)
    assert answer.hits == [] and answer.notice is not None
    assert QUESTION_MASKED in answer.notice

    [hit] = search_materials(session, TEAM, f"야간 반품 {PHONE}").hits
    assert hit.material_id == row.id and PHONE not in hit.excerpt


def test_the_words_are_in_no_statement_log_line_or_result(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bound parameter is part of a failed statement's message, and the
    engine does not hide parameters: nothing of the question may be bound."""
    upload(session, RETURNS, title="물류 운영 메모")
    upload(session, ROOMS, title="환불 보류 안내")
    rule(monkeypatch, floor=NOTHING_PASSES)
    sent: list[str] = []

    def record(
        conn: Connection, cursor: object, statement: str, parameters: object, *_: object
    ) -> None:
        sent.append(f"{statement} {parameters!r}")

    event.listen(session.get_bind(), "before_cursor_execute", record)
    with capture_logs() as logs:
        answer = search_materials(session, TEAM, f"환불 보류 {PHONE}")
        missed = search_materials(session, TEAM, "야간 환불")
    event.remove(session.get_bind(), "before_cursor_execute", record)

    assert [hit.title for hit in answer.hits] == ["환불 보류 안내"] and missed.hits == []
    assert sent and not any(word in line for line in sent for word in ("환불", "보류", "야간"))
    said = repr(logs) + repr(missed) + repr(answer).replace("환불 보류 안내", "")
    assert not any(word in said for word in ("환불", "보류", PHONE))
    assert logs == [
        {
            "event": "extraction_material_searched",
            "team_id": TEAM,
            "hits": 1,
            "dropped": 0,
            "by_title": 1,
            "by_words": 0,
            "log_level": "info",
        },
        {
            "event": "extraction_material_searched",
            "team_id": TEAM,
            "hits": 0,
            "dropped": 0,
            "by_title": 0,
            "by_words": 0,
            "log_level": "info",
        },
    ]


def test_the_log_counts_what_the_words_found(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    both(session)
    rule(monkeypatch, floor=NOTHING_PASSES)

    with capture_logs() as logs:
        search_materials(session, TEAM, SHORT)

    [line] = logs
    assert (line["hits"], line["by_title"], line["by_words"]) == (2, 1, 1)


# --- 3. the screen ------------------------------------------------------------------------


def test_what_the_words_find_is_screened_on_the_way_out(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    titled, body = both(session)
    for chunk in session.scalars(select(ExtMaterialChunk)):
        chunk.text += f"야간 반품 접수 담당 {PHONE}\n"
    titled.title = f"야간 반품 접수 {PHONE}"
    session.flush()
    rule(monkeypatch, floor=NOTHING_PASSES)

    hits = search_materials(session, TEAM, SHORT).hits

    assert [hit.material_id for hit in hits] == [titled.id, body.id]
    assert PHONE not in repr(hits)
    assert hits[0].title == "야간 반품 접수 ***-****-****"
    assert "***-****-****" in hits[1].excerpt


def test_a_word_hit_the_screen_refuses_is_dropped(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    titled, body = both(session)
    rule(monkeypatch, floor=NOTHING_PASSES)
    refused = {titled.title}

    def screen(text: str) -> str:
        if text in refused or any(mark in text for mark in refused):
            raise PrivacyViolationError("refused")
        return text

    monkeypatch.setattr(material_search, "screen_output", screen)
    with capture_logs() as logs:
        answer = search_materials(session, TEAM, SHORT)
    assert [hit.material_id for hit in answer.hits] == [body.id] and answer.more is False
    assert (logs[0]["dropped"], logs[0]["by_title"], logs[0]["by_words"]) == (1, 0, 1)

    refused.add("창구를 한 곳으로")
    answer = search_materials(session, TEAM, SHORT)
    assert answer.hits == [] and answer.notice == NOTHING_CLOSE


# --- 4. beside the floor and the cut --------------------------------------------------------


def shelf(session: Session) -> tuple[ExtMaterial, ExtMaterial, ExtMaterial, dict[str, float]]:
    """Three files for the question ``SHORT``: one that says more of it than
    the others, one that holds its words among much else, one that holds
    them nowhere. Their scores, to set a floor between."""
    near = upload(session, "야간 반품 접수.\n", title="가")
    far = upload(session, RETURNS + ROOMS + PIPES, title="나")
    none = upload(session, "야간 접수 반송.\n", title="다")
    scores = {row.id: score_of(session, SHORT, row) for row in (near, far, none)}
    assert scores[near.id] > scores[none.id] > scores[far.id]
    return near, far, none, scores


def test_the_materials_over_the_floor_are_the_same_with_the_word_match_and_without(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    near, far, none, scores = shelf(session)
    between = (scores[none.id] + scores[far.id]) / 2
    rule(monkeypatch, floor=between, cut=2.0, words=OFF)
    assert ids(session, SHORT) == [near.id, none.id]

    rule(monkeypatch, words=NO_FLOOR)

    assert ids(session, SHORT) == [near.id, none.id, far.id]


def test_a_piece_found_by_its_words_is_cut_from_the_best_material_like_any_other(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    near, far, none, scores = shelf(session)
    between = (scores[none.id] + scores[far.id]) / 2
    reaches = scores[near.id] - scores[far.id]
    rule(monkeypatch, floor=between, cut=reaches - 0.001, words=NO_FLOOR)
    assert ids(session, SHORT) == [near.id, none.id]

    rule(monkeypatch, cut=reaches + 0.001)
    assert ids(session, SHORT) == [near.id, none.id, far.id]


def test_with_nothing_over_the_floor_the_cut_is_from_the_best_piece_holding_the_words(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``none`` scores between the two and holds no word: it is no hit, and
    it is not what the cut is measured from."""
    near, far, none, scores = shelf(session)
    apart = scores[near.id] - scores[far.id]
    rule(monkeypatch, floor=NOTHING_PASSES, cut=apart - 0.001)
    assert ids(session, SHORT) == [near.id]

    rule(monkeypatch, cut=apart + 0.001)
    assert ids(session, SHORT) == [near.id, far.id]


def test_title_matches_come_first_then_the_vectors_then_the_pieces(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    near, far, none, scores = shelf(session)
    titled = upload(session, ROOMS, title="야간 반품 접수 안내")
    assert score_of(session, SHORT, titled) < scores[far.id]
    rule(monkeypatch, floor=(scores[none.id] + scores[far.id]) / 2)

    answer = search_materials(session, TEAM, SHORT)

    assert [hit.material_id for hit in answer.hits] == [titled.id, near.id, none.id, far.id]
    assert answer.more is False


def test_a_material_is_one_hit_however_many_ways_it_is_found(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the title, over the floor, and in a piece under it."""
    row = upload(session, "야간 반품 접수.\n" + ROOMS * 12 + RETURNS, title="야간 반품 접수")
    assert session.query(ExtMaterialChunk).count() > 1
    best = score_of(session, SHORT, row)
    rule(monkeypatch, floor=best - 0.001)

    answer = search_materials(session, TEAM, SHORT)

    [hit] = answer.hits
    assert (hit.material_id, hit.score) == (row.id, round(best, 4))
    assert answer.more is False


def test_five_at_most_and_more_counts_what_the_words_found(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(4):
        upload(session, ROOMS + f"{n}번 안내.\n", title=f"야간 반품 접수 {n}")
    for n in range(3):
        upload(session, RETURNS + f"{n}번 메모.\n", title=f"메모 {n}")
    rule(monkeypatch, floor=NOTHING_PASSES)

    answer = search_materials(session, TEAM, SHORT)

    assert len(answer.hits) == 5 and answer.more is True
    assert [hit.title.startswith("야간") for hit in answer.hits] == [True] * 4 + [False]
    assert len(search_materials(session, TEAM, SHORT, limit=2).hits) == 2


def test_the_text_of_at_most_a_thousand_pieces_is_read_for_words(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The piece that holds the words is further than ``WORD_CANDIDATES``
    others: it is not read."""
    monkeypatch.setattr(material_search, "WORD_CANDIDATES", 3)
    assert WORD_CANDIDATES == 1000
    held = upload(session, RETURNS + ROOMS + PIPES, title="나")
    for n in range(3):
        upload(session, f"야간 접수 반송 {n}.\n", title=f"다 {n}")
    rule(monkeypatch, floor=NOTHING_PASSES)
    assert ids(session, SHORT) == []

    monkeypatch.setattr(material_search, "WORD_CANDIDATES", 4)
    assert ids(session, SHORT) == [held.id]


# --- the route --------------------------------------------------------------------------------


def test_the_route_answers_with_what_the_words_found(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    titled, body = both(session)
    monkeypatch.setattr(get_settings(), "material_upload", True)
    rule(monkeypatch, floor=NOTHING_PASSES)
    app = FastAPI()
    app.include_router(router, prefix="/api/extraction")
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    client = TestClient(app)

    response = client.post(
        f"/api/extraction/materials/search?team_id={TEAM}", json={"question": SHORT}
    )

    assert response.status_code == 200
    assert [hit["material_id"] for hit in response.json()["hits"]] == [titled.id, body.id]
    assert "question" not in response.text and "score" not in response.text
