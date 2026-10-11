"""A team searches its uploaded materials, and the assistant can too (#817).

The rules under test (``docs/architecture/privacy.md``, "Uploaded documents are
masked before storage"; #817 10(b), 2026-10-08):

- a piece's vector is made from its masked text and nothing else, in the
  upload request, and is as wide as the stored column;
- the question is masked before it is embedded, and is not stored, returned
  or logged -- by the service, the route or the tool;
- a search reads only the asking team's uploads, and a deleted material is
  not found;
- an excerpt is short, one per material, around the line that answers, and
  screened on its way out;
- a material under the similarity floor is not a hit, and with none left the
  answer is empty and says nothing close enough was found;
- the note that stars are hidden values comes only with a run the masker made;
- the assistant's tool is off by default, and on, names no other team's
  material and repeats nothing of the question.

Every value here is invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Meeting, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError, ValidationError
from autune_extraction import material_search, materials, tools
from autune_extraction.config import ExtractionSettings, get_settings
from autune_extraction.material_search import (
    ASKS_FOR_PERSONAL,
    EXCERPT_CHARS,
    MAX_QUESTION_CHARS,
    NOTHING_CLOSE,
    QUESTION_MASKED,
    STARRED,
    excerpt_of,
    search_materials,
    shows_masked_run,
)
from autune_extraction.models import MATERIAL_EMBEDDING_DIM, ExtMaterial, ExtMaterialChunk
from autune_extraction.pipeline import FakeEmbedder
from autune_extraction.router import router

from .conftest import sign_in

TEAM = "team_1"
OTHER = "team_2"
PREFIX = "/api/extraction"
SEARCH = f"{PREFIX}/materials/search?team_id={TEAM}"

PHONE = "010-2345-6789"
LAUNCH = "출시 일정은 11월 둘째 주로 잡는다. 출시 전 점검은 QA 팀이 맡는다.\n"
BUDGET = "예산 집행 내역: 장비 구매 1200만원, 외주 개발 800만원.\n"
QUESTION = f"출시 일정 담당 {PHONE}"


class Recording(FakeEmbedder):
    """The material fake, keeping every text it was handed."""

    def __init__(self) -> None:
        super().__init__(dim=MATERIAL_EMBEDDING_DIM)
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return super().embed(texts)


@pytest.fixture(autouse=True)
def no_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake counts shared characters, so its scores mean nothing on the
    real embedder's scale: these tests set the floor themselves -- none, here,
    and a chosen one where the floor is what is tested."""
    monkeypatch.setattr(get_settings(), "material_search_min_similarity", -1.0)


def set_floor(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(get_settings(), "material_search_min_similarity", value)


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> Recording:
    fake = Recording()
    monkeypatch.setattr(materials, "get_material_embedder", lambda: fake)
    monkeypatch.setattr(material_search, "get_material_embedder", lambda: fake)
    return fake


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember, Meeting)}
    owned = {"ext_materials", "ext_material_chunks", "ext_material_alarms"}
    tables = [t for name, t in Base.metadata.tables.items() if name in shared | owned]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s


def upload(session: Session, body: str, *, team: str = TEAM, title: str = "자료") -> ExtMaterial:
    return materials.store_upload(
        session, team, title=title, file_name="plan.txt", data=body.encode()
    )


# --- vectors ----------------------------------------------------------------------


def test_each_piece_gets_a_vector_of_the_columns_width_made_from_masked_text(
    session: Session, embedder: Recording
) -> None:
    row = upload(session, (LAUNCH + f"담당 연락처 {PHONE}\n") * 20)

    chunks = list(session.scalars(select(ExtMaterialChunk).order_by(ExtMaterialChunk.position)))
    assert len(chunks) > 1
    assert all(len(chunk.embedding) == MATERIAL_EMBEDDING_DIM for chunk in chunks)  # type: ignore[arg-type]
    assert row.embedding_model == "fake"
    # The embedder was handed the stored pieces, which are masked -- and nothing else.
    assert embedder.seen == [chunk.text for chunk in chunks]
    assert not any(PHONE in text for text in embedder.seen)


class Narrow(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(dim=64)


def test_an_embedder_of_another_width_is_refused_and_nothing_is_stored(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materials, "get_material_embedder", Narrow)

    with pytest.raises(RuntimeError) as refused:
        upload(session, LAUNCH)

    assert "width 1024" in str(refused.value) and "출시" not in str(refused.value)
    assert session.scalars(select(ExtMaterial)).first() is None


# --- searching --------------------------------------------------------------------


def test_the_nearest_material_comes_first_with_a_short_excerpt(
    session: Session, embedder: Recording
) -> None:
    launch = upload(session, LAUNCH * 3, title="출시 계획")
    budget = upload(session, BUDGET * 3, title="예산")

    answer = search_materials(session, TEAM, "출시 일정 점검")

    assert [hit.material_id for hit in answer.hits] == [launch.id, budget.id]
    first = answer.hits[0]
    assert (first.title, first.position) == ("출시 계획", 0)
    assert "출시 일정" in first.excerpt
    assert len(first.excerpt) <= EXCERPT_CHARS + 2


def test_the_question_is_masked_before_it_is_embedded_and_kept_nowhere(
    session: Session, embedder: Recording
) -> None:
    upload(session, LAUNCH)
    embedder.seen.clear()

    with capture_logs() as logs:
        answer = search_materials(session, TEAM, QUESTION)

    [embedded] = embedder.seen
    assert PHONE not in embedded and "출시 일정" in embedded
    assert answer.notice is not None and QUESTION_MASKED in answer.notice
    said = repr(answer) + repr(logs)
    assert PHONE not in said and "담당" not in said
    assert logs == [
        {
            "event": "extraction_material_searched",
            "team_id": TEAM,
            "hits": 1,
            "dropped": 0,
            "log_level": "info",
        }
    ]


def test_only_the_asking_teams_uploads_are_read(session: Session, embedder: Recording) -> None:
    session.add(Team(id=OTHER, name="다른 팀"))
    ours = upload(session, BUDGET, title="우리 예산")
    upload(session, LAUNCH, team=OTHER, title="남의 출시")

    answer = search_materials(session, TEAM, "출시 일정")

    assert [hit.material_id for hit in answer.hits] == [ours.id]
    assert "남의" not in repr(answer) and "출시 일정은" not in repr(answer)


def test_a_deleted_material_is_not_found(session: Session, embedder: Recording) -> None:
    row = upload(session, LAUNCH)
    assert search_materials(session, TEAM, "출시").hits

    materials.delete_material(session, TEAM, row.id)

    assert search_materials(session, TEAM, "출시").hits == []


def test_a_drive_link_and_a_piece_of_another_model_are_never_hits(
    session: Session, embedder: Recording
) -> None:
    materials.register(session, TEAM, title="출시 링크", link="a" * 20)
    old = upload(session, LAUNCH)
    old.embedding_model = "another-model"
    session.flush()

    assert search_materials(session, TEAM, "출시").hits == []


def test_one_hit_a_material_at_most_five_and_more_says_so(
    session: Session, embedder: Recording
) -> None:
    for n in range(7):
        upload(session, LAUNCH * 4, title=f"자료 {n}")

    answer = search_materials(session, TEAM, "출시 일정")

    assert len(answer.hits) == 5 and answer.more is True
    assert len({hit.material_id for hit in answer.hits}) == 5
    assert len(search_materials(session, TEAM, "출시", limit=2).hits) == 2


def test_an_excerpt_and_a_title_are_screened_on_the_way_out(
    session: Session, embedder: Recording
) -> None:
    """Stored text was masked when stored; the screen is for a detector that
    learned a shape since, and for a typed title."""
    row = upload(session, LAUNCH)
    chunk = session.scalars(select(ExtMaterialChunk)).one()
    chunk.text = f"출시 일정 담당자 연락처 {PHONE}"
    row.title = f"출시 {PHONE}"
    session.flush()

    [hit] = search_materials(session, TEAM, "출시 일정").hits

    assert PHONE not in hit.excerpt and PHONE not in hit.title
    assert "***-****-****" in hit.excerpt


@pytest.mark.parametrize("question", ["", "   ", "가" * (MAX_QUESTION_CHARS + 1)])
def test_a_blank_or_overlong_question_is_refused_by_the_rule(
    session: Session, embedder: Recording, question: str
) -> None:
    with pytest.raises(ValidationError) as refused:
        search_materials(session, TEAM, question)

    assert refused.value.details == {"field": "question"}
    assert "가가" not in str(refused.value)
    assert embedder.seen == []


def test_asking_for_a_hidden_kind_of_value_says_it_cannot_be_shown(
    session: Session, embedder: Recording
) -> None:
    upload(session, LAUNCH + f"담당 연락처 {PHONE}\n")

    answer = search_materials(session, TEAM, "담당자 전화번호")

    assert answer.notice is not None
    assert ASKS_FOR_PERSONAL in answer.notice and STARRED in answer.notice
    assert all(PHONE not in hit.excerpt for hit in answer.hits)


def test_no_notice_when_nothing_was_hidden(session: Session, embedder: Recording) -> None:
    upload(session, LAUNCH)

    assert search_materials(session, TEAM, "출시 일정").notice is None


def test_a_lone_star_in_an_excerpt_says_nothing_of_hidden_values(
    session: Session, embedder: Recording
) -> None:
    upload(session, "* 출시 일정은 11월이다. **중요** 점검은 QA 팀이 맡는다.\n***\n")

    [hit] = search_materials(session, TEAM, "출시 일정").hits

    assert "*" in hit.excerpt
    assert search_materials(session, TEAM, "출시 일정").notice is None


@pytest.mark.parametrize(
    ("excerpt", "masked"),
    [
        ("연락처 ***-****-****", True),
        ("메일 *****@*******.***", True),
        ("번호 ************ 끝", True),
        ("* 항목 하나", False),
        ("**굵게** 쓴 말", False),
        ("각주*", False),
        ("위\n***\n아래", False),
        ("*-*", False),
    ],
)
def test_only_a_run_of_the_maskers_shape_counts_as_hidden(excerpt: str, masked: bool) -> None:
    assert shows_masked_run(excerpt) is masked


# --- the floor --------------------------------------------------------------------


def test_the_floor_has_a_default_for_the_real_embedder_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    field = ExtractionSettings.model_fields["material_search_min_similarity"]
    assert field.default == 0.4

    monkeypatch.setenv("AUTUNE_EXTRACTION_MATERIAL_SEARCH_MIN_SIMILARITY", "0.55")
    assert ExtractionSettings().material_search_min_similarity == 0.55
    monkeypatch.setenv("AUTUNE_EXTRACTION_MATERIAL_SEARCH_MIN_SIMILARITY", "1.5")
    with pytest.raises(PydanticValidationError):
        ExtractionSettings()


def test_a_material_under_the_floor_is_not_a_hit(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = upload(session, LAUNCH * 3, title="출시 계획")
    budget = upload(session, BUDGET * 3, title="예산")
    question = "출시 일정 점검"
    scores = {hit.material_id: hit.score for hit in search_materials(session, TEAM, question).hits}
    assert scores[launch.id] > scores[budget.id]
    set_floor(monkeypatch, (scores[launch.id] + scores[budget.id]) / 2)

    answer = search_materials(session, TEAM, question)

    assert [hit.material_id for hit in answer.hits] == [launch.id]
    assert answer.more is False and answer.notice is None


def test_nothing_over_the_floor_is_an_empty_answer_that_says_so(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(session, LAUNCH * 3, title="출시 계획")
    upload(session, BUDGET * 3, title="예산")
    set_floor(monkeypatch, 0.99)

    with capture_logs() as logs:
        answer = search_materials(session, TEAM, "출시 일정 점검")

    assert answer.hits == [] and answer.more is False
    assert answer.notice == NOTHING_CLOSE
    assert logs[0]["hits"] == 0 and "출시" not in repr(logs)


def test_an_empty_answer_still_says_the_question_was_masked(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(session, LAUNCH)
    set_floor(monkeypatch, 0.99)

    answer = search_materials(session, TEAM, QUESTION)

    assert answer.hits == []
    assert answer.notice == f"{NOTHING_CLOSE} {QUESTION_MASKED}"
    assert PHONE not in repr(answer)


def test_an_excerpt_is_cut_around_the_question_and_marked_where_cut() -> None:
    text = "가나다 " * 60 + "출시 일정은 11월이다 " + "라마바 " * 60

    cut = excerpt_of(text, "출시 일정")

    assert cut.startswith("…") and cut.endswith("…")
    assert "출시 일정은" in cut
    assert len(cut) <= EXCERPT_CHARS + 2
    assert excerpt_of("짧은 글", "출시") == "짧은 글"
    assert excerpt_of(text, "없는 말").startswith("가나다")


ROLLBACK = (
    "# 배포 체크리스트\n\n"
    "배포 전에 모든 테스트가 통과했는지 확인한다. 스테이징 환경에서 주요 화면을 직접 "
    "눌러 보고, 로그에 오류가 없는지 본다. 데이터베이스 마이그레이션은 배포 전날 리뷰를 "
    "받는다. 알림 채널에 배포 시작을 알리고 담당자를 지정한다. 트래픽이 적은 시간대를 "
    "고른다.\n"
    "모니터링 대시보드를 띄워 두고 오류율과 응답 시간을 지켜본다.\n"
    "롤백 절차: 문제가 생기면 직전 릴리스 태그로 되돌리고, 마이그레이션은 down "
    "스크립트로 되돌린 뒤 알림 채널에 공지한다.\n"
    "배포 후 30분 동안 지표를 확인한다. 이상이 없으면 알림 채널에 완료를 알린다. "
    "다음 배포 일정은 회고에서 정한다. 회고 문서는 위키에 남긴다. 체크리스트는 분기마다 "
    "다시 본다.\n"
)


def test_the_excerpt_is_the_window_around_the_line_that_answers() -> None:
    """The hand-tried case: the old excerpt began at the heading and cut
    before the rollback line."""
    line = "롤백 절차: 문제가 생기면 직전 릴리스 태그로 되돌리고"
    assert line not in ROLLBACK[:EXCERPT_CHARS]

    cut = excerpt_of(ROLLBACK, "배포할 때 롤백은 어떻게 하나요")

    assert line in cut
    assert cut.startswith("…") and cut.endswith("…")
    assert len(cut) <= EXCERPT_CHARS + 2


def test_the_rollback_excerpt_reaches_the_person_through_the_search(
    session: Session, embedder: Recording
) -> None:
    upload(session, ROLLBACK, title="배포 체크리스트")

    [hit] = search_materials(session, TEAM, "배포할 때 롤백은 어떻게 하나요").hits

    assert "롤백 절차" in hit.excerpt and len(hit.excerpt) <= EXCERPT_CHARS + 2


def test_the_window_is_chosen_from_the_masked_question_only() -> None:
    """A word the masker hid is not looked for: ``***`` holds no letters."""
    text = "가나다 " * 40 + "연락처는 ***-****-**** 이다. " + "라마바 " * 40

    assert excerpt_of(text, "***-****-****").startswith("가나다")


# --- the route --------------------------------------------------------------------


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


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "material_upload", True)


def test_off_with_uploads_the_route_is_not_there(client: TestClient) -> None:
    answer = client.post(SEARCH, json={"question": "출시"})

    assert answer.status_code == 404 and "error" not in answer.json()


@pytest.mark.usefixtures("enabled")
def test_a_member_searches_and_the_question_comes_back_nowhere(
    client: TestClient, session: Session, embedder: Recording
) -> None:
    row = upload(session, LAUNCH, title="출시 계획")

    with capture_logs() as logs:
        answer = client.post(SEARCH, json={"question": QUESTION})

    assert answer.status_code == 200
    body = answer.json()
    assert body["more"] is False and QUESTION_MASKED in body["notice"]
    [hit] = body["hits"]
    assert (hit["material_id"], hit["title"], hit["position"]) == (row.id, "출시 계획", 0)
    assert "출시 일정" in hit["excerpt"]
    assert PHONE not in answer.text and "담당" not in answer.text
    assert PHONE not in repr(logs) and "담당" not in repr(logs)


@pytest.mark.usefixtures("enabled")
def test_somebody_not_on_the_team_gets_the_404_of_an_unknown_team(
    client: TestClient, session: Session, embedder: Recording
) -> None:
    session.add(Team(id=OTHER, name="다른 팀"))
    upload(session, LAUNCH, team=OTHER)

    answer = client.post(f"{PREFIX}/materials/search?team_id={OTHER}", json={"question": "출시"})

    assert answer.status_code == 404 and answer.json()["error"]["code"] == "not_found"
    assert "출시 일정은" not in answer.text


@pytest.mark.usefixtures("enabled")
def test_a_member_searches_by_one_of_the_teams_meetings(
    client: TestClient, session: Session, embedder: Recording
) -> None:
    """The pre-meeting brief knows its meeting, not its team (#1147 B2)."""
    session.add(Meeting(id="mtg_1", team_id=TEAM, title="주간 회의"))
    row = upload(session, LAUNCH, title="출시 계획")

    answer = client.post(
        f"{PREFIX}/materials/search?meeting_id=mtg_1", json={"question": "출시 일정"}
    )

    assert answer.status_code == 200
    assert [hit["material_id"] for hit in answer.json()["hits"]] == [row.id]


@pytest.mark.usefixtures("enabled")
def test_another_teams_meeting_gets_the_404_of_an_unknown_meeting(
    client: TestClient, session: Session, embedder: Recording
) -> None:
    session.add(Team(id=OTHER, name="다른 팀"))
    session.add(Meeting(id="mtg_2", team_id=OTHER, title="다른 팀 회의"))
    upload(session, LAUNCH, team=OTHER, title="남의 계획")

    answer = client.post(f"{PREFIX}/materials/search?meeting_id=mtg_2", json={"question": "출시"})

    assert answer.status_code == 404 and answer.json()["error"]["code"] == "not_found"
    assert "남의" not in answer.text and "출시 일정은" not in answer.text


def test_off_with_uploads_a_meeting_scoped_search_is_not_there_either(
    client: TestClient, session: Session
) -> None:
    session.add(Meeting(id="mtg_1", team_id=TEAM, title="주간 회의"))

    answer = client.post(f"{PREFIX}/materials/search?meeting_id=mtg_1", json={"question": "출시"})

    assert answer.status_code == 404 and "error" not in answer.json()


def test_neither_team_nor_meeting_is_the_404_of_an_unknown_team(client: TestClient) -> None:
    rules = client.get(f"{PREFIX}/materials/upload-rules")

    assert rules.status_code == 404 and rules.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("on", [False, True])
def test_the_upload_rules_answer_by_one_of_the_teams_meetings(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch, on: bool
) -> None:
    monkeypatch.setattr(get_settings(), "material_upload", on)
    session.add(Team(id=OTHER, name="다른 팀"))
    session.add(Meeting(id="mtg_1", team_id=TEAM, title="주간 회의"))
    session.add(Meeting(id="mtg_2", team_id=OTHER, title="다른 팀 회의"))

    ours = client.get(f"{PREFIX}/materials/upload-rules?meeting_id=mtg_1")
    theirs = client.get(f"{PREFIX}/materials/upload-rules?meeting_id=mtg_2")

    assert ours.status_code == 200
    assert (ours.json()["enabled"], ours.json()["search"]) == (on, on)
    assert theirs.status_code == 404


@pytest.mark.usefixtures("enabled")
def test_an_overlong_question_is_refused_without_repeating_it(
    client: TestClient, embedder: Recording
) -> None:
    long = "김민수 " * 100

    answer = client.post(SEARCH, json={"question": long})

    assert answer.status_code == 422
    assert answer.json()["error"]["details"] == {"field": "question"}
    assert "김민수" not in answer.text


# --- the assistant's tool ---------------------------------------------------------


@pytest.fixture
def tool_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "material_search_tool", True)


def test_the_tool_is_collected_says_when_to_use_it_and_takes_the_run_scope() -> None:
    assert tools.find_materials in tools.TOOLS
    assert tools.find_materials not in tools.ACTIONS
    assert (tools.find_materials.__doc__ or "").startswith("Use this")


def test_off_by_default_the_tool_searches_nothing(session: Session, embedder: Recording) -> None:
    upload(session, LAUNCH)
    embedder.seen.clear()

    result = tools.find_materials(session, TEAM, "출시 일정")

    assert result["ok"] is False and result["items"] == []
    assert embedder.seen == []


@pytest.mark.usefixtures("tool_on")
def test_the_tool_answers_with_excerpts_and_ids_and_nothing_of_the_question(
    session: Session, embedder: Recording
) -> None:
    session.add(Team(id=OTHER, name="다른 팀"))
    ours = upload(session, LAUNCH, title="출시 계획")
    theirs = upload(session, LAUNCH, team=OTHER, title="남의 계획")

    with capture_logs() as logs:
        result = tools.find_materials(session, TEAM, QUESTION)

    assert result["ok"] is True
    assert result["evidence"] == [ours.id]
    [item] = result["items"]
    assert (item["id"], item["title"], item["position"]) == (ours.id, "출시 계획", 0)
    assert "출시 일정" in item["body"]
    said = json.dumps(result, ensure_ascii=False) + repr(logs)
    assert PHONE not in said and "담당" not in said
    assert theirs.id not in said and "남의" not in said


@pytest.mark.usefixtures("tool_on")
@pytest.mark.parametrize("question", ["", "가" * (MAX_QUESTION_CHARS + 1), 42])
def test_the_tool_refuses_a_bad_question_by_the_rule(
    session: Session, embedder: Recording, question: object
) -> None:
    result = tools.find_materials(session, TEAM, question)  # type: ignore[arg-type]

    assert result["ok"] is False and "question must be" in result["reason"]
    assert "가가" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.usefixtures("tool_on")
def test_the_tool_answers_nothing_close_enough_as_the_search_does(
    session: Session, embedder: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(session, LAUNCH, title="출시 계획")
    set_floor(monkeypatch, 0.99)

    result = tools.find_materials(session, TEAM, "출시 일정")

    assert result["ok"] is True
    assert result["items"] == [] and result["evidence"] == []
    assert result["summary"] == NOTHING_CLOSE
