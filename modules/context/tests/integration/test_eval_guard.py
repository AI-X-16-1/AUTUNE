"""The evaluation harness refuses a database holding meetings it did not create."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from autune_context.eval import _guard
from autune_core import Meeting, Team, session_scope


@pytest.fixture
def make_team(
    db_engine: object,
) -> Iterator[Callable[[str], None]]:  # db_engine ensures migrations ran
    created: list[str] = []

    def _make(name: str) -> None:
        with session_scope() as s:
            team = Team(name=name)
            s.add(team)
            s.flush()
            s.add(
                Meeting(
                    team_id=team.id,
                    title="회의",
                    status="analyzing",
                    started_at=datetime.now(tz=UTC),
                )
            )
            created.append(team.id)

    yield _make
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id.in_(created)))


def test_a_real_meeting_blocks_the_run(make_team: Callable[[str], None]) -> None:
    make_team("a real team")

    with pytest.raises(_guard.RealMeetingsPresentError, match="throwaway database"):
        _guard.refuse_real_meetings()


def test_a_team_left_by_a_killed_eval_run_does_not_count(make_team: Callable[[str], None]) -> None:
    before = _guard.real_meeting_count()

    make_team(f"{_guard.EVAL_TEAM_PREFIX}tl-07")

    assert _guard.real_meeting_count() == before


def test_the_prefix_is_matched_literally(make_team: Callable[[str], None]) -> None:
    """``eval_`` must not pass as ``eval-`` through a LIKE wildcard."""
    before = _guard.real_meeting_count()

    make_team("eval_x")

    assert _guard.real_meeting_count() == before + 1
