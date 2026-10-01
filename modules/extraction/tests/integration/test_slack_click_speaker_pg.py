"""A click on the DM's buttons counts only when the speaker made it.

Slack signs the request, not the person (#610 review). Under test: the
speaker's own linked Slack account, in the workspace the meeting's team
installed, is recorded; another Slack account, or the right account from
another workspace, is not. On PostgreSQL: the integration settings are JSONB.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Meeting, Participant, Team, User, Utterance
from autune_core.entities import TeamIntegration, UserIntegration
from autune_extraction import service
from autune_extraction.confirmations import ConfirmationResponse
from autune_extraction.models import ExtConfirmation

MEETING = "mtg_slackclick"
UTTERANCE = "utt_slackclick"
TEAM = "team_slackclick"
KIM, LEE = "user_sc_kim", "user_sc_lee"
WORKSPACE = "T_TEAM"


@pytest.fixture
def session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    s = db_session
    s.add(Team(id=TEAM, name="팀"))
    s.add_all(
        [
            User(id=KIM, email=f"{KIM}@example.com", display_name="김"),
            User(id=LEE, email=f"{LEE}@example.com", display_name="이"),
        ]
    )
    s.flush()
    s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
    s.flush()
    s.add(Participant(id="par_sc_kim", meeting_id=MEETING, speaker_label="김", user_id=KIM))
    s.flush()
    s.add(
        Utterance(
            id=UTTERANCE,
            meeting_id=MEETING,
            participant_id="par_sc_kim",
            speaker_label="김",
            start_sec=0.0,
            end_sec=1.0,
            text="그럼 제가 한번 볼게요",
        )
    )
    s.flush()
    s.add(
        ExtConfirmation(
            utterance_id=UTTERANCE,
            meeting_id=MEETING,
            reason="weak_assent",
            sent_at=datetime.now(UTC),
        )
    )
    s.add(TeamIntegration(team_id=TEAM, service="slack", config={"workspace_id": WORKSPACE}))
    s.add_all(
        [
            UserIntegration(user_id=KIM, service="slack", config={"slack_user_id": "U_SC_KIM"}),
            UserIntegration(user_id=LEE, service="slack", config={"slack_user_id": "U_SC_LEE"}),
        ]
    )
    s.flush()

    @contextmanager
    def scope() -> Iterator[Session]:
        yield s
        s.flush()

    monkeypatch.setattr(service, "session_scope", scope)
    yield s


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[ConfirmationResponse]:
    seen: list[ConfirmationResponse] = []
    monkeypatch.setattr(service, "apply_confirmation_response", seen.append)
    return seen


def click(member: str, workspace: str = WORKSPACE) -> ConfirmationResponse:
    return ConfirmationResponse(
        utterance_id=UTTERANCE,
        resolved_kind=UtteranceKind.COMMITMENT,
        responder_id=member,
        workspace_id=workspace,
    )


def test_the_speakers_own_click_is_recorded(session: Session, recorded: list) -> None:
    service.answer_from_slack(click("U_SC_KIM"))

    assert [r.responder_id for r in recorded] == ["U_SC_KIM"]


def test_someone_elses_click_is_dropped(session: Session, recorded: list) -> None:
    service.answer_from_slack(click("U_SC_LEE"))

    assert recorded == []


def test_an_account_nobody_linked_is_dropped(session: Session, recorded: list) -> None:
    service.answer_from_slack(click("U_SC_NOBODY"))

    assert recorded == []


def test_a_click_from_another_workspace_is_dropped(session: Session, recorded: list) -> None:
    service.answer_from_slack(click("U_SC_KIM", workspace="T_OTHER"))

    assert recorded == []
