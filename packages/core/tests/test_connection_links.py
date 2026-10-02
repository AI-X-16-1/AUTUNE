"""What a connection's status hands a screen to link to: the team's own Jira site
and Slack channel, and nothing a stored row could turn into another kind of link."""

from __future__ import annotations

import pytest

from autune_core.auth_router import _https_link, _slack_channel_link


def test_a_jira_site_is_a_link_only_when_it_is_https() -> None:
    assert _https_link("https://acme.atlassian.net") == "https://acme.atlassian.net"


@pytest.mark.parametrize(
    "stored",
    [None, "", "http://acme.atlassian.net", "javascript:alert(1)", "//acme.atlassian.net", 7],
)
def test_anything_else_stored_as_the_site_is_no_link(stored: object) -> None:
    """The value is an ``href`` on the settings screen. A row from before the
    site was kept, or one holding another scheme, shows the connection without
    a link rather than a link to somewhere else."""
    assert _https_link(stored) is None


def test_the_slack_link_opens_the_teams_channel_in_its_workspace() -> None:
    config = {"workspace_id": "T0AB12", "channel": "C09XYZ", "channel_name": "autune"}

    assert _slack_channel_link(config) == "https://app.slack.com/client/T0AB12/C09XYZ"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"workspace_id": "T0AB12"},
        {"channel": "C09XYZ"},
        {"workspace_id": "", "channel": "C09XYZ"},
        {"workspace_id": "T0AB12", "channel": "C09/../admin"},
        {"workspace_id": "T0AB12?x=", "channel": "C09XYZ"},
    ],
)
def test_without_both_ids_shaped_like_slacks_there_is_no_link(config: dict) -> None:
    """An install from before the ids were stored has no channel to point at,
    and an id that is not one must not become part of an address."""
    assert _slack_channel_link(config) is None
