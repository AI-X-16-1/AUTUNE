"""A team searches its uploaded materials on PostgreSQL, and the assistant can too (#817).

What the unit suite cannot show on SQLite: that pgvector's cosine operator
ranks the pieces, inside the asking team only; that a deleted material, or a
deleted team, leaves no vector to find; that the assistant's run records the
tool's name and ids and nothing of the question (#817 10(b)); and that the
revision adding the vectors comes off and goes back on.

Every value here is invented.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_agent.main.registry import CallBudget, RunScope, Toolbox, collect_tools
from autune_core import Team, get_settings
from autune_extraction import materials
from autune_extraction.config import get_settings as extraction_settings
from autune_extraction.material_search import search_materials
from autune_extraction.models import ExtMaterialChunk

PHONE = "010-2345-6789"
LAUNCH = "출시 일정은 11월 둘째 주로 잡는다. 출시 전 점검은 QA 팀이 맡는다.\n"
BUDGET = "예산 집행 내역: 장비 구매 1200만원, 외주 개발 800만원.\n"
QUESTION = f"출시 일정 담당 {PHONE}"
ALEMBIC = ["uv", "run", "alembic", "-c", "infra/alembic.ini"]


def new_team(session: Session, name: str) -> str:
    team = Team(name=name)
    session.add(team)
    session.flush()
    return team.id


@pytest.fixture
def team_id(db_session: Session) -> str:
    return new_team(db_session, "팀")


def upload(session: Session, team_id: str, body: str, title: str = "자료") -> str:
    row = materials.store_upload(
        session, team_id, title=title, file_name="plan.txt", data=body.encode()
    )
    return row.id


def test_pgvector_ranks_the_teams_pieces_and_no_other_teams(
    db_session: Session, team_id: str
) -> None:
    other = new_team(db_session, "다른 팀")
    launch = upload(db_session, team_id, LAUNCH * 3, "출시 계획")
    budget = upload(db_session, team_id, BUDGET * 3, "예산")
    upload(db_session, other, LAUNCH * 3, "남의 출시")

    answer = search_materials(db_session, team_id, "출시 일정 점검")

    assert [hit.material_id for hit in answer.hits] == [launch, budget]
    assert answer.hits[0].score > answer.hits[1].score
    assert "남의" not in repr(answer)


def test_a_deleted_material_and_a_deleted_team_leave_nothing_to_find(
    db_session: Session, team_id: str
) -> None:
    gone = upload(db_session, team_id, LAUNCH)
    kept = upload(db_session, team_id, BUDGET)

    materials.delete_material(db_session, team_id, gone)

    assert [hit.material_id for hit in search_materials(db_session, team_id, "출시").hits] == [kept]
    db_session.execute(sa.delete(Team).where(Team.id == team_id))
    left = db_session.scalar(
        sa.select(sa.func.count())
        .select_from(ExtMaterialChunk)
        .where(ExtMaterialChunk.material_id.in_([gone, kept]))
    )
    assert left == 0
    assert search_materials(db_session, team_id, "출시").hits == []


def test_the_assistants_run_keeps_the_tools_name_and_ids_and_not_the_question(
    db_session: Session, team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extraction_settings(), "material_search_tool", True)
    other = new_team(db_session, "다른 팀")
    ours = upload(db_session, team_id, LAUNCH, "출시 계획")
    theirs = upload(db_session, other, LAUNCH, "남의 출시")
    budget = CallBudget()
    toolbox = Toolbox(
        collect_tools(),
        db_session,
        budget,
        allowed=["extraction.find_materials"],
        scope=RunScope(team_id=team_id, user_id="user_asker"),
    )

    with capture_logs() as logs:
        result = toolbox.call("extraction.find_materials", question=QUESTION)
        # A model that names another team's id is refused, not answered.
        refused = toolbox.call("extraction.find_materials", team_id=other, question="출시")

    assert result.ok and result.evidence == [ours]
    assert refused.ok is False and refused.items == []
    # What agent_runs stores is ``budget.steps`` (``main/store.py``).
    assert budget.steps[0] == {
        "tool": "extraction.find_materials",
        "ok": True,
        "evidence": [ours],
        "truncated": False,
    }
    said = json.dumps(budget.steps) + result.model_dump_json() + repr(logs)
    assert PHONE not in said and "담당" not in said
    assert theirs not in said and "남의" not in said


def column_names(table: str) -> set[str]:
    engine = sa.create_engine(get_settings().database_url)
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "alembic.ini").is_file():
            return parent
    raise RuntimeError("could not locate repo root (infra/alembic.ini not found)")


def test_the_vector_revision_comes_off_and_goes_back_on(db_engine: sa.Engine) -> None:
    root = repo_root()
    assert "embedding" in column_names("ext_material_chunks")

    subprocess.run([*ALEMBIC, "downgrade", "5d1a7c3e9b24"], check=True, cwd=root)
    assert "embedding" not in column_names("ext_material_chunks")
    assert "embedding_model" not in column_names("ext_materials")
    assert "text" in column_names("ext_material_chunks")

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
    assert "embedding" in column_names("ext_material_chunks")
    assert "embedding_model" in column_names("ext_materials")
