"""Follow-up's proposal, approved: C puts the follow-up meeting on the
approver's own calendar, invites the meeting's team members who took part and
tells the team's Slack channel (``tools.schedule_followup_meeting``).

SQLite, the harness ``test_read_endpoints`` uses. Google is a fake standing in
for ``CalendarClient``, and Slack the team channel ``test_calendar_writes``
fakes: what is under test is the event asked for, what is recorded and what
is said -- not the HTTP client.
"""

# ruff: noqa: F401, F811  -- fixtures shared with test_read_endpoints and test_calendar_writes

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import Meeting, Participant, TeamMember, User
from autune_gap import followup_meeting, tools
from autune_gap.models import GapAgendaEvent, GapFollowupEvent, GapGap
from autune_integrations import IntegrationError, ReconnectRequiredError
from autune_integrations.errors import SlackRecipientNotLinkedError

from .test_calendar_writes import TEAMMATE, FakeSlack, TeamSlack, calendars, slack
from .test_read_endpoints import (
    FOREIGN_MEETING,
    MEETING,
    MEMBER,
    OUTSIDER,
    TEAM,
    gap,
    queued,
    session,
)

KST = followup_meeting.KST
DAY = datetime.now(KST).date() + timedelta(days=3)


class FakeGoogle:
    """The approver's calendar: every event made, with the query it was sent with."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.made: list[dict[str, Any]] = []
        self.params: list[dict[str, Any]] = []
        self.fail_with = fail_with

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert (method, path) == ("POST", "/calendars/primary/events")
        if self.fail_with is not None:
            raise self.fail_with
        self.made.append(kwargs["json"])
        self.params.append(kwargs.get("params") or {})
        return {"id": f"evt_{len(self.made)}"}


class DmSlack(FakeSlack):
    """The team's Slack, DMs too: every DM sent, as ``(user id, text, blocks)``.
    Only the users in ``linked`` linked an account; one in ``failing`` is a
    send Slack does not take."""

    def __init__(self, linked: set[str], failing: set[str] | None = None) -> None:
        super().__init__()
        self.linked = linked
        self.failing = failing or set()
        self.dms: list[tuple[str, str, list[dict[str, Any]]]] = []

    def send_dm(self, user_id: str, text: str, blocks: list[dict[str, Any]]) -> str:
        if user_id not in self.linked:
            raise SlackRecipientNotLinkedError("not linked")
        if user_id in self.failing:
            raise IntegrationError("down")
        self.dms.append((user_id, text, blocks))
        return "1.0"


def connect(slack: TeamSlack, *, linked: set[str] | None = None, **kwargs: Any) -> DmSlack:
    """The team's Slack, with ``linked`` (by default nobody) able to get a DM."""
    client = DmSlack(linked or set(), **kwargs)
    slack.client = client
    return client


@pytest.fixture
def meeting(session: Session) -> Meeting:
    """``MEETING`` held at 14:00 in Korea for 50 minutes, with the approver,
    a teammate and somebody from another team taking part, and three gaps:
    a high one, a low one and a dismissed one."""
    session.add(User(id=TEAMMATE, email="Teammate@Example.com", display_name="팀원"))
    session.add(TeamMember(team_id=TEAM, user_id=TEAMMATE))
    row = session.get(Meeting, MEETING)
    assert row is not None
    row.started_at = datetime(2026, 10, 1, 14, 0, tzinfo=KST).astimezone(UTC)
    row.duration_seconds = 50 * 60
    for index, user_id in enumerate((MEMBER, TEAMMATE, OUTSIDER, None)):
        session.add(
            Participant(meeting_id=MEETING, user_id=user_id, speaker_label=f"Speaker {index}")
        )
    gap(session, "gap_high")
    gap(session, "gap_low", risk_score=0.1)
    gap(session, "gap_gone", dismissed=True)
    low = session.get(GapGap, "gap_low")
    assert low is not None
    low.severity = "low"
    session.flush()
    return row


def schedule(session: Session, day: date = DAY, **kwargs: Any) -> dict[str, Any]:
    return tools.schedule_followup_meeting(
        session,
        team_id=kwargs.get("team_id", TEAM),
        meeting_id=kwargs.get("meeting_id", MEETING),
        due_date=day.isoformat(),
        user_id=kwargs.get("user_id", MEMBER),
    )


def test_approval_puts_the_meeting_on_the_approvers_calendar(
    session: Session, meeting: Meeting, calendars: dict[str, Any], slack: TeamSlack
) -> None:
    google = calendars[MEMBER] = FakeGoogle()

    result = schedule(session)

    assert result["ok"] is True
    [event] = google.made
    assert event["summary"] == "후속 회의 · 주간 회의"
    assert event["start"]["dateTime"] == datetime.combine(DAY, time(14, 0), KST).isoformat()
    assert event["end"]["dateTime"] == datetime.combine(DAY, time(15, 0), KST).isoformat()
    assert event["description"].startswith("[Autune 갭] ")
    assert event["description"].endswith("(gap_high)")
    assert "gap_low" not in event["description"]
    assert "gap_gone" not in event["description"]
    assert google.params == [{"sendUpdates": "all", "fields": "id"}]
    assert "1명을 초대" in result["summary"]


def test_only_team_members_who_took_part_are_invited(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    """The approver organises it; somebody of another team who was in the
    meeting, and a speaker nobody identified, are not invited."""
    google = calendars[MEMBER] = FakeGoogle()

    schedule(session)

    assert google.made[0]["attendees"] == [{"email": "teammate@example.com"}]


def test_the_event_and_its_lines_are_recorded(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    calendars[MEMBER] = FakeGoogle()

    schedule(session)

    [row] = session.scalars(select(GapFollowupEvent)).all()
    assert (row.meeting_id, row.user_id, row.event_id, row.event_day) == (
        MEETING,
        MEMBER,
        "evt_1",
        DAY,
    )
    [line] = session.scalars(select(GapAgendaEvent)).all()
    assert (line.gap_id, line.user_id, line.event_id, line.event_day) == (
        "gap_high",
        MEMBER,
        "evt_1",
        DAY,
    )
    high = session.get(GapGap, "gap_high")
    assert high is not None and high.carried_at is not None


def test_the_day_is_offered_as_a_picked_day_afterwards(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    calendars[MEMBER] = FakeGoogle()

    schedule(session)

    [row] = tools.next_meeting_days(session, TEAM, MEETING)["items"]
    assert row["days"] == [{"day": DAY.isoformat(), "picked_by": [MEMBER]}]


def test_the_team_channel_is_told_once(
    session: Session, meeting: Meeting, calendars: dict[str, Any], slack: TeamSlack
) -> None:
    calendars[MEMBER] = FakeGoogle()
    connect(slack)

    first = schedule(session)
    second = schedule(session)

    card = slack.card()
    assert "후속 회의" in card and "주간 회의" in card
    assert "14:00" in card and "초대 1명" in card
    assert "성능 요구사항이 정해지지 않았습니다" in card
    assert "슬랙 채널에 알렸습니다" in first["summary"]
    assert second["ok"] is False


def test_a_second_approval_makes_no_second_event(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    google = calendars[MEMBER] = FakeGoogle()

    schedule(session)
    again = schedule(session, day=DAY + timedelta(days=1))

    assert again["ok"] is False
    assert again["reason"] == "the meeting already has its follow-up event"
    assert len(google.made) == 1


@pytest.mark.parametrize(
    ("calendar", "reason"),
    [
        (None, "the approver has no calendar connected"),
        (ReconnectRequiredError("gone"), "the approver's calendar must be connected again"),
        (FakeGoogle(fail_with=IntegrationError("down")), "the calendar did not take the event"),
    ],
)
def test_a_calendar_that_does_not_take_it_leaves_nothing_and_can_be_tried_again(
    session: Session,
    meeting: Meeting,
    calendars: dict[str, Any],
    slack: TeamSlack,
    calendar: Any,
    reason: str,
) -> None:
    if calendar is not None:
        calendars[MEMBER] = calendar
    connect(slack)

    failed = schedule(session)

    assert (failed["ok"], failed["reason"]) == (False, reason)
    assert session.scalars(select(GapFollowupEvent)).all() == []
    assert session.scalars(select(GapAgendaEvent)).all() == []
    assert slack.client is not None and slack.client.posted == []

    google = calendars[MEMBER] = FakeGoogle()
    assert schedule(session)["ok"] is True
    assert len(google.made) == 1


def test_a_day_already_past_makes_nothing(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    google = calendars[MEMBER] = FakeGoogle()

    result = schedule(session, day=datetime.now(KST).date() - timedelta(days=1))

    assert (result["ok"], result["reason"]) == (False, "the day has passed")
    assert google.made == []


def test_another_teams_meeting_reads_as_missing(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    google = calendars[MEMBER] = FakeGoogle()

    result = schedule(session, meeting_id=FOREIGN_MEETING)

    assert result["ok"] is False and google.made == []


def test_an_approver_off_the_team_is_refused(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    google = calendars[OUTSIDER] = FakeGoogle()

    result = schedule(session, user_id=OUTSIDER)

    assert result["ok"] is False and google.made == []


def test_text_that_is_not_a_date_is_refused(session: Session, meeting: Meeting) -> None:
    result = tools.schedule_followup_meeting(session, TEAM, MEETING, "next week", MEMBER)

    assert (result["ok"], result["reason"]) == (False, "not a date: 'next week'")


@pytest.mark.parametrize(
    ("minutes", "length"),
    [(None, 60), (10, 30), (50, 60), (100, 90), (200, 120)],
)
def test_the_event_lasts_as_long_as_the_meeting_in_half_hours(
    minutes: int | None, length: int
) -> None:
    held = Meeting(id="mtg_x", team_id=TEAM, title="t", started_at=None)
    held.duration_seconds = minutes * 60 if minutes is not None else None

    starts, ends = followup_meeting.window(held, DAY)

    assert starts == datetime.combine(DAY, time(10, 0), KST)
    assert ends - starts == timedelta(minutes=length)


def test_the_cards_basis_is_accepted_and_changes_nothing(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    """Follow-up's proposal carries ``basis`` for the card, as B's write took it."""
    google = calendars[MEMBER] = FakeGoogle()

    result = tools.schedule_followup_meeting(
        session, TEAM, MEETING, DAY.isoformat(), MEMBER, basis="cadence"
    )

    assert result["ok"] is True
    assert "cadence" not in str(google.made)


def test_the_team_reads_its_follow_up_ahead_without_who_approved_it(
    session: Session, meeting: Meeting, calendars: dict[str, Any]
) -> None:
    assert tools.upcoming_followup(session, TEAM)["items"] == []
    calendars[MEMBER] = FakeGoogle()

    schedule(session)

    read = tools.upcoming_followup(session, TEAM)
    assert read["items"] == [{"title": "후속 회의", "meeting_id": MEETING, "day": DAY.isoformat()}]
    assert MEMBER not in str(read)
    assert tools.upcoming_followup(session, "team_2")["items"] == []


def test_a_follow_up_already_held_is_not_ahead(session: Session, meeting: Meeting) -> None:
    session.add(
        GapFollowupEvent(
            meeting_id=MEETING,
            user_id=MEMBER,
            calendar_id="primary",
            event_id="evt_old",
            event_day=datetime.now(KST).date() - timedelta(days=1),
        )
    )
    session.flush()

    assert tools.upcoming_followup(session, TEAM)["items"] == []


def test_each_guest_who_linked_slack_is_sent_a_dm(
    session: Session, meeting: Meeting, calendars: dict[str, Any], slack: TeamSlack
) -> None:
    """The approver organises it and the other team's member is no guest, so
    only the teammate is sent one -- even though all three linked Slack."""
    calendars[MEMBER] = FakeGoogle()
    client = connect(slack, linked={MEMBER, TEAMMATE, OUTSIDER})

    result = schedule(session)

    [(to, text, blocks)] = client.dms
    assert to == TEAMMATE
    assert "후속 회의" in text and "14:00" in text and "주간 회의" in text
    assert "성능 요구사항이 정해지지 않았습니다" in str(blocks)
    assert "참석자 1명에게 슬랙 DM" in result["summary"]
    assert len(client.posted) == 1, "the team channel is still told once"


def test_a_guest_without_a_linked_slack_gets_the_invitation_alone(
    session: Session, meeting: Meeting, calendars: dict[str, Any], slack: TeamSlack
) -> None:
    google = calendars[MEMBER] = FakeGoogle()
    client = connect(slack)

    result = schedule(session)

    assert result["ok"] is True
    assert client.dms == []
    assert "DM" not in result["summary"]
    assert google.made[0]["attendees"] == [{"email": "teammate@example.com"}]


def test_a_dm_slack_does_not_take_leaves_the_meeting_made(
    session: Session, meeting: Meeting, calendars: dict[str, Any], slack: TeamSlack
) -> None:
    calendars[MEMBER] = FakeGoogle()
    connect(slack, linked={TEAMMATE}, failing={TEAMMATE})

    result = schedule(session)

    assert result["ok"] is True
    assert len(session.scalars(select(GapFollowupEvent)).all()) == 1


def test_no_dm_without_team_slack(
    session: Session, meeting: Meeting, calendars: dict[str, Any], slack: TeamSlack
) -> None:
    calendars[MEMBER] = FakeGoogle()

    result = schedule(session)

    assert result["ok"] is True and "DM" not in result["summary"]
