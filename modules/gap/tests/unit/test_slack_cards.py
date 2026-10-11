"""S20's "질문 카드 Slack 전송" (#824, plan 3): the meeting's open ``high``
gaps go to the team's Slack channel as question cards, one message each, a
few at most, and one line with a link counts the rest.

The routes run on the harness ``test_read_endpoints`` uses, and the team's
channel is ``test_calendar_writes``' fake.
"""

# ruff: noqa: F401, F811  -- fixtures shared with test_read_endpoints

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_core.errors import PrivacyViolationError
from autune_gap import team_notice
from autune_gap.models import GapGap

from .test_calendar_writes import TeamSlack, slack
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
    assert "다른 high 갭 2건" in posted[-1]
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
