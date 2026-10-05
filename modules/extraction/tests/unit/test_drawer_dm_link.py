"""The drawer's link to the reader's own confirmation DM (#680, the user, 2026-10-04).

Slack opens a DM for its two members only. The rule under test: the link is
there for the speaker the DM went to and for nobody else, and only when the DM
was placed and the team is still on Slack.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtConfirmation

MEETING = "mtg_1"
SPEAKER = "user_speaker"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    # team_integrations is Postgres-only (JSONB): the team's Slack, as stored.
    monkeypatch.setattr(
        service,
        "load_integration",
        lambda *_: SimpleNamespace(config={"workspace_id": "T_ACME"}),
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(Participant(id="par_1", meeting_id=MEETING, speaker_label="A", user_id=SPEAKER))
        s.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                participant_id="par_1",
                speaker_label="A",
                start_sec=0.0,
                end_sec=1.0,
                text="네 그렇게 해 보죠",
            )
        )
        s.add(
            ExtConfirmation(
                utterance_id="utt_1",
                meeting_id=MEETING,
                reason="weak_assent",
                sent_at=datetime(2026, 10, 1, tzinfo=UTC),
                dm_channel="D_SPEAKER",
                dm_ts="17000.1",
            )
        )
        s.add(
            ExtActionItem(
                id="act_1",
                meeting_id=MEETING,
                description="그렇게 해 보기",
                status="todo",
                confidence=0.9,
                origin="model",
            )
        )
        s.add(ExtActionItemSource(action_item_id="act_1", utterance_id="utt_1"))
        s.flush()
        yield s


def item(session: Session) -> ExtActionItem:
    found = session.get(ExtActionItem, "act_1")
    assert found is not None
    return found


def test_the_speaker_gets_a_link_to_their_own_dm(session: Session) -> None:
    detail = service.read_detail(session, item(session), reader_id=SPEAKER)

    assert detail.confirmation_dm_url == (
        "https://slack.com/app_redirect?team=T_ACME&channel=D_SPEAKER"
    )


def test_anyone_else_on_the_team_gets_none(session: Session) -> None:
    assert (
        service.read_detail(session, item(session), reader_id="user_other").confirmation_dm_url
        is None
    )
    assert service.read_detail(session, item(session)).confirmation_dm_url is None


def test_a_dm_never_placed_is_no_link(session: Session) -> None:
    row = session.get(ExtConfirmation, "utt_1")
    assert row is not None
    row.dm_channel = None
    session.flush()

    assert service.confirmation_dm_url(session, item(session), SPEAKER) is None


def test_a_team_no_longer_on_slack_is_no_link(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "load_integration", lambda *_: None)

    assert service.confirmation_dm_url(session, item(session), SPEAKER) is None
