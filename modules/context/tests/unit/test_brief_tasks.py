"""The two brief tasks -- ``periodic.send_due_briefs`` and ``send_brief``.

``briefs`` and the session are patched out: these pin what the tasks do with
``briefs``'s answers (enqueue, post, stay quiet). tests/integration covers the
queries behind them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from autune_context import tasks
from autune_context.briefs import Brief
from autune_core import is_periodic_task_name
from autune_core.periodic import schedule_of

MEETING = "mtg_brief"


@contextmanager
def _no_session() -> Iterator[MagicMock]:
    yield MagicMock()


@pytest.fixture(autouse=True)
def _session() -> Iterator[None]:
    with patch.object(tasks, "session_scope", _no_session):
        yield


def _brief() -> Brief:
    return Brief(
        meeting_id=MEETING,
        title="주간 회의",
        starts_at=datetime.now(tz=UTC) + timedelta(minutes=10),
        recap=None,
        recap_gone=False,
        match_reason=None,
        agenda=(),
        sent_at=None,
    )


def test_send_due_briefs_is_scheduled_every_minute() -> None:
    assert is_periodic_task_name(tasks.send_due_briefs.name)
    assert schedule_of(tasks.send_due_briefs) == timedelta(minutes=1)


def test_every_due_meeting_gets_its_own_send_brief() -> None:
    with (
        patch.object(tasks.briefs, "due_meeting_ids", return_value=["mtg_a", "mtg_b"]),
        patch.object(tasks, "send_brief") as send,
    ):
        tasks.send_due_briefs()

    assert [c.args for c in send.delay.call_args_list] == [("mtg_a",), ("mtg_b",)]


def test_brief_is_posted_to_the_team_channel() -> None:
    config = MagicMock()
    config.require_secret.return_value = "xoxb-test"
    slack = MagicMock()
    with (
        patch.object(tasks, "_slack_target", return_value=("C0TEAM", config)),
        patch.object(tasks.briefs, "compose_due_brief", return_value=_brief()) as compose,
        patch.object(tasks, "SlackClient", return_value=slack),
    ):
        tasks.send_brief(MEETING)

    assert compose.call_args.kwargs["will_send"] is True
    channel, fallback, _blocks = slack.post_message.call_args.args
    assert channel == "C0TEAM"
    assert fallback == "10분 뒤 회의: 주간 회의"


def test_a_team_without_slack_still_gets_a_brief_but_nothing_is_posted() -> None:
    with (
        patch.object(tasks, "_slack_target", return_value=None),
        patch.object(tasks.briefs, "compose_due_brief", return_value=_brief()) as compose,
        patch.object(tasks, "SlackClient") as slack_client,
    ):
        tasks.send_brief(MEETING)

    assert compose.call_args.kwargs["will_send"] is False
    slack_client.assert_not_called()


def test_a_brief_claimed_elsewhere_or_no_longer_due_posts_nothing() -> None:
    with (
        patch.object(tasks, "_slack_target", return_value=("C0TEAM", MagicMock())),
        patch.object(tasks.briefs, "compose_due_brief", return_value=None),
        patch.object(tasks, "SlackClient") as slack_client,
    ):
        tasks.send_brief(MEETING)

    slack_client.assert_not_called()
