"""Per-person integration credentials (#59, #435): the table's shape and the
guards, pinned the way ``test_integrations_config`` pins the team table --
without a database."""

from __future__ import annotations

import pytest

from autune_core import Base, UserIntegrationConfig
from autune_core.errors import ValidationError
from autune_core.user_integrations import USER_SERVICES, _check_service

TABLE = "user_integrations"


def test_repr_does_not_leak_the_secret() -> None:
    config = UserIntegrationConfig(
        service="calendar", user_id="usr_1", secret="1//refresh", config={"calendar_id": "primary"}
    )
    assert "1//refresh" not in repr(config)
    assert "secret=<set>" in repr(config)


def test_unknown_service_is_refused() -> None:
    with pytest.raises(ValidationError):
        _check_service("notion")  # a team connects Notion, not a person


def test_gmail_waits_for_its_decision() -> None:
    """#431 is open; the schema does not get ahead of it."""
    with pytest.raises(ValidationError):
        _check_service("gmail")


def test_services_match_the_check_constraint() -> None:
    check = next(
        c for c in Base.metadata.tables[TABLE].constraints if c.name == f"ck_{TABLE}_service"
    )
    for service in USER_SERVICES:
        assert f"'{service}'" in str(check.sqltext)


def test_credentials_go_when_the_person_goes() -> None:
    """#59: a person's grant has the opposite lifetime of a team's connection."""
    fk = next(iter(Base.metadata.tables[TABLE].c.user_id.foreign_keys))
    assert fk.column.table.name == "users"
    assert fk.ondelete == "CASCADE"


def test_one_row_per_person_and_service() -> None:
    names = {c.name for c in Base.metadata.tables[TABLE].constraints}
    assert f"uq_{TABLE}_user_service" in names


def test_table_is_shared_not_module_owned() -> None:
    assert not TABLE.startswith(("aud_", "ext_", "gap_", "ctx_", "intel_"))


def test_one_slack_member_is_one_person() -> None:
    """#478: a partial unique index over confirmed Slack links, so two people
    confirming one Slack account at once cannot both hold it."""
    from autune_core.entities import UserIntegration

    (index,) = [
        i
        for i in UserIntegration.__table__.indexes
        if i.name == "uq_user_integrations_slack_member"
    ]
    where = str(index.dialect_options["postgresql"]["where"])

    assert index.unique
    assert "slack_user_id" in str(list(index.expressions)[0])
    assert "service = 'slack'" in where
