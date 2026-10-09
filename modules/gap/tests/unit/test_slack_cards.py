"""S20's "질문 카드 Slack 전송" (#824, plan 3): the meeting's open ``high``
gaps go to the team's Slack channel as question cards, one message each, a
few at most, and one line with a link counts the rest.

The routes run on the harness ``test_read_endpoints`` uses, and the team's
channel is ``test_calendar_writes``' fake.
"""

# ruff: noqa: F401, F811  -- fixtures shared with test_read_endpoints

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_contracts import MeetingReportPosted
from autune_core.errors import PrivacyViolationError
from autune_gap import service, tasks, team_notice
from autune_gap.models import GapGap, GapReportThread

from .test_calendar_writes import TEAMMATE, TeamSlack, slack, teammate
from .test_read_endpoints import (
    FOREIGN_MEETING,
    MEETING,
    PREFIX,
    client,
    gap,
    session,
)

ROUTE = f"{PREFIX}/reports/{MEETING}/slack"


def with_question(session: Session, gap_id: str, question: str) -> str:
    row = session.get(GapGap, gap_id)
    assert row is not None
    row.suggested_question = question
    session.flush()
    return gap_id


def cards(slack: TeamSlack) -> list[str]:
    """Each message posted, text and blocks, as one string."""
    assert slack.client is not None
    return [
        "\n".join([text, *(json.dumps(b, ensure_ascii=False) for b in blocks)])
        for _, text, blocks in slack.client.posted
    ]


def test_each_open_high_gap_is_its_own_card_most_risky_first(
    client: TestClient, session: Session, slack: TeamSlack
) -> None:
    with_question(session, gap(session, "gap_low_risk", risk_score=0.7), "둘째 질문")
    with_question(session, gap(session, "gap_top", risk_score=0.95), "첫째 질문")
    slack.connect()

    response = client.post(ROUTE)

    assert response.json() == {"meeting_id": MEETING, "high": 2, "sent": 2, "slack": "posted"}
    first, second = cards(slack)
    assert "첫째 질문" in first and "둘째 질문" not in first
    assert "둘째 질문" in second
    assert "<@" not in first + second


def test_a_dismissed_or_lower_gap_is_not_sent(
    client: TestClient, session: Session, slack: TeamSlack
) -> None:
    gap(session, "gap_dismissed", dismissed=True)
    medium = session.get(GapGap, gap(session, "gap_medium"))
    assert medium is not None
    medium.severity = "medium"
    session.flush()
    channel = slack.connect()

    response = client.post(ROUTE)

    assert response.json() == {"meeting_id": MEETING, "high": 0, "sent": 0, "slack": "not_tried"}
    assert channel.posted == []


def test_past_the_cap_one_line_counts_the_rest_and_links_the_report(
    client: TestClient, session: Session, slack: TeamSlack
) -> None:
    for n in range(team_notice.SENT + 2):
        gap(session, f"gap_{n}", risk_score=0.9 - n / 100)
    slack.connect()

    response = client.post(ROUTE)

    assert response.json()["sent"] == team_notice.SENT
    assert response.json()["high"] == team_notice.SENT + 2
    posted = cards(slack)
    assert len(posted) == team_notice.SENT + 1
    assert "위험도가 높은 다른 갭 2건" in posted[-1]
    assert f"/meetings/{MEETING}/gap|" in posted[-1]


def test_a_title_cannot_mention_the_whole_channel(
    client: TestClient, session: Session, slack: TeamSlack
) -> None:
    row = session.get(GapGap, gap(session, "gap_1"))
    assert row is not None
    row.title = "<!channel> 확인"
    session.flush()
    slack.connect()

    client.post(ROUTE)

    (card,) = cards(slack)
    assert "<!channel>" not in card
    assert "&lt;!channel&gt;" in card


def test_the_first_card_refused_stops_the_rest_and_is_logged_by_id(
    client: TestClient, session: Session, slack: TeamSlack
) -> None:
    gap(session, "gap_1")
    gap(session, "gap_2", risk_score=0.8)
    channel = slack.connect(fail_with=PrivacyViolationError("unmasked 010-1234-5678"))

    with capture_logs() as logs:
        response = client.post(ROUTE)

    assert response.json() == {"meeting_id": MEETING, "high": 2, "sent": 0, "slack": "refused"}
    assert channel.posted == []
    refused = [e for e in logs if e["event"] == "gap_slack_refused"]
    assert [(e["meeting_id"], e["gap_id"]) for e in refused] == [(MEETING, "gap_1")]
    assert "010-1234-5678" not in json.dumps(logs, ensure_ascii=False, default=str)


def test_without_team_slack_nothing_is_sent_and_it_says_so(
    client: TestClient, session: Session
) -> None:
    gap(session, "gap_1")

    response = client.post(ROUTE)

    assert response.json() == {"meeting_id": MEETING, "high": 1, "sent": 0, "slack": "no_slack"}


def test_another_teams_meeting_is_a_404_and_posts_nothing(
    client: TestClient, session: Session, slack: TeamSlack
) -> None:
    gap(session, "gap_foreign", meeting_id=FOREIGN_MEETING)
    channel = slack.connect()

    response = client.post(f"{PREFIX}/reports/{FOREIGN_MEETING}/slack")

    assert response.status_code == 404
    assert channel.posted == []


# --- in the thread of E's report (MeetingReportPosted) ------------------------


@pytest.fixture
def scoped(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """``record_report_thread`` opens its own ``session_scope``; here it is the
    test's session."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.flush()

    monkeypatch.setattr(service, "session_scope", scope)
    return session


def report_posted(channel: str = "C_TEAM", meeting_id: str = MEETING) -> dict[str, str]:
    return MeetingReportPosted(
        meeting_id=meeting_id, channel=channel, thread_ts="111.222"
    ).model_dump(mode="json")


def test_once_e_posted_the_report_the_cards_reply_in_its_thread(
    client: TestClient, session: Session, slack: TeamSlack, scoped: Session
) -> None:
    with_question(session, gap(session, "gap_1"), "<!here> 목표는 누가 정합니까?")
    tasks.on_intelligence_meeting_report_posted(report_posted())
    channel = slack.connect()

    response = client.post(ROUTE)

    assert response.json()["slack"] == "posted"
    assert channel.posted == []
    ((where, thread_ts, text),) = channel.replied
    assert (where, thread_ts) == ("C_TEAM", "111.222")
    assert "*갭 질문* · 주간 회의" in text
    assert "&lt;!here&gt; 목표는 누가 정합니까?" in text
    assert "<!here>" not in text


def test_asking_a_member_replies_in_the_reports_thread_too(
    client: TestClient, session: Session, slack: TeamSlack, scoped: Session, teammate: str
) -> None:
    gap(session, "gap_1")
    tasks.on_intelligence_meeting_report_posted(report_posted())
    slack.members[TEAMMATE] = "U_MATE"
    channel = slack.connect()

    client.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": TEAMMATE})

    ((_, thread_ts, text),) = channel.replied
    assert thread_ts == "111.222"
    assert "<@U_MATE>" in text


def test_a_thread_on_a_channel_the_team_has_left_is_not_used(
    client: TestClient, session: Session, slack: TeamSlack, scoped: Session
) -> None:
    gap(session, "gap_1")
    tasks.on_intelligence_meeting_report_posted(report_posted(channel="C_OLD"))
    channel = slack.connect()

    client.post(ROUTE)

    assert channel.replied == []
    assert len(channel.posted) == 1


def test_the_thread_is_kept_once_and_the_latest_wins(session: Session, scoped: Session) -> None:
    tasks.on_intelligence_meeting_report_posted(report_posted())
    tasks.on_intelligence_meeting_report_posted(report_posted(channel="C_NEW"))

    rows = session.scalars(select(GapReportThread)).all()
    assert [(r.meeting_id, r.channel, r.thread_ts) for r in rows] == [(MEETING, "C_NEW", "111.222")]


def test_a_report_for_a_meeting_gone_since_keeps_nothing(session: Session, scoped: Session) -> None:
    kept = service.record_report_thread(
        MeetingReportPosted(meeting_id="mtg_gone", channel="C_TEAM", thread_ts="1.0")
    )

    assert kept is False
    assert session.scalars(select(GapReportThread)).all() == []
