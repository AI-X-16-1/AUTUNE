"""B -> D: a team's open Jira issues for the pre-meeting brief (#436)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autune_contracts import (
    AGENDA_TITLE_MAX,
    EVENTS,
    EXTRACTION_AGENDA_CHANGED,
    AgendaIssue,
    TeamAgenda,
    fixtures,
    validate_major_version,
)


def test_the_event_is_declared() -> None:
    assert EXTRACTION_AGENDA_CHANGED in EVENTS


def test_a_title_alone_is_an_issue() -> None:
    issue = AgendaIssue(title="이슈")
    assert (issue.key, issue.status, issue.url) == (None, None, None)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.atlassian.net/browse/AUT-1",
        "https://example.atlassian.net/browse/AUT-1?x=<script>",
        "https://example.atlassian.net/jira/software/projects/AUT",
        "https://example.atlassian.net/browse/AUT-1 ",
        "//example.atlassian.net/browse/AUT-1",
    ],
)
def test_a_link_that_is_not_an_https_issue_page_is_refused(url: str) -> None:
    """D renders ``url`` as an ``href``; the contract is where that is safe."""
    with pytest.raises(ValidationError):
        AgendaIssue(title="이슈", url=url)


def test_an_empty_title_or_a_malformed_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        AgendaIssue(title="")
    with pytest.raises(ValidationError):
        AgendaIssue(title="이슈", key="aut-1")


def test_an_empty_agenda_is_representable() -> None:
    """A team whose last issue closed: D must be able to clear what it showed."""
    agenda = TeamAgenda.model_validate(fixtures.load("team_agenda") | {"issues": []})
    assert agenda.issues == []


def test_the_agenda_is_version_checked_like_a_meeting_payload() -> None:
    agenda = TeamAgenda.model_validate(fixtures.load("team_agenda"))
    validate_major_version(agenda)
    older = TeamAgenda.model_validate(fixtures.load("team_agenda") | {"contract_version": "1.0"})
    with pytest.raises(ValueError, match="major version mismatch"):
        validate_major_version(older)


def test_it_is_about_a_team() -> None:
    with pytest.raises(ValidationError):
        TeamAgenda.model_validate(fixtures.load("team_agenda") | {"team_id": "mtg_1"})


@pytest.mark.parametrize("as_of", ["2026-09-29T08:50:00", "2026-09-29"])
def test_a_time_without_an_offset_is_refused(as_of: str) -> None:
    """D keeps the latest ``as_of``; naive against aware cannot be compared."""
    with pytest.raises(ValidationError):
        TeamAgenda.model_validate(fixtures.load("team_agenda") | {"as_of": as_of})


def test_a_title_is_bounded() -> None:
    AgendaIssue(title="가" * AGENDA_TITLE_MAX)
    with pytest.raises(ValidationError):
        AgendaIssue(title="가" * (AGENDA_TITLE_MAX + 1))
