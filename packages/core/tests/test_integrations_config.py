"""Per-team integration configuration: encryption, storage shape, guards.

Database round-trips belong in an integration test against real Postgres. What
is pinned here is what a unit test can pin and what would be expensive to get
wrong: that a secret never sits in a column as plaintext, that it does not leak
through a log line, and that the table is shaped so a deleted team takes its
credentials with it.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from autune_core import Base, IntegrationConfig, crypto
from autune_core.errors import ConfigurationError, ValidationError
from autune_core.integrations_config import SERVICES, _check_service
from autune_core.settings import Settings

TABLE = "team_integrations"


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A real Fernet key, with the cached instance cleared around the test."""
    value = Fernet.generate_key().decode()
    monkeypatch.setenv("AUTUNE_ENCRYPTION_KEY", value)
    crypto._fernet.cache_clear()
    from autune_core import settings as settings_module

    settings_module.get_settings.cache_clear()
    yield value
    crypto._fernet.cache_clear()
    settings_module.get_settings.cache_clear()


def test_secret_round_trips(key: str) -> None:
    assert crypto.decrypt(crypto.encrypt("ntn_token")) == "ntn_token"


def test_ciphertext_does_not_contain_the_secret(key: str) -> None:
    """What lands in the column must not be readable in a database dump."""
    assert "ntn_token" not in crypto.encrypt("ntn_token")


def test_a_different_key_cannot_decrypt(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    ciphertext = crypto.encrypt("ntn_token")
    monkeypatch.setenv("AUTUNE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    from autune_core import settings as settings_module

    settings_module.get_settings.cache_clear()
    with pytest.raises(ConfigurationError):
        crypto.decrypt(ciphertext)


def test_missing_key_fails_with_a_usable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_ENCRYPTION_KEY", "")
    crypto._fernet.cache_clear()
    from autune_core import settings as settings_module

    settings_module.get_settings.cache_clear()
    with pytest.raises(ConfigurationError, match="AUTUNE_ENCRYPTION_KEY"):
        crypto.encrypt("anything")
    crypto._fernet.cache_clear()
    settings_module.get_settings.cache_clear()


def test_repr_does_not_leak_the_secret() -> None:
    """An f-string in a log line must not publish a customer's token."""
    config = IntegrationConfig(service="notion", team_id="team_1", secret="ntn_token")
    assert "ntn_token" not in repr(config)
    assert "ntn_token" not in f"{config}"


def test_require_secret_explains_what_is_missing() -> None:
    config = IntegrationConfig(service="jira", team_id="team_1")
    with pytest.raises(ValidationError, match="jira"):
        config.require_secret()


def test_require_names_the_missing_key() -> None:
    config = IntegrationConfig(service="jira", team_id="team_1", config={"project_key": "AUT"})
    assert config.require("project_key") == "AUT"
    with pytest.raises(ValidationError, match="transition_todo"):
        config.require("transition_todo")


def test_unknown_service_is_refused() -> None:
    with pytest.raises(ValidationError):
        _check_service("dropbox")


def test_services_match_the_check_constraint() -> None:
    """The tuple and the database constraint drift apart silently otherwise."""
    constraint = next(
        c for c in Base.metadata.tables[TABLE].constraints if c.name == f"ck_{TABLE}_service"
    )
    for service in SERVICES:
        assert f"'{service}'" in str(constraint.sqltext)


def test_credentials_go_when_the_team_goes(key: str) -> None:
    """A deleted team must not leave working tokens behind."""
    fk = next(iter(Base.metadata.tables[TABLE].c.team_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_connection_outlives_the_person_who_made_it() -> None:
    fk = next(iter(Base.metadata.tables[TABLE].c.connected_by.foreign_keys))
    assert fk.ondelete == "SET NULL"


def test_one_row_per_team_and_service() -> None:
    names = {c.name for c in Base.metadata.tables[TABLE].constraints}
    assert f"uq_{TABLE}_team_service" in names


def test_table_is_shared_not_module_owned() -> None:
    assert not TABLE.startswith(("aud_", "ext_", "gap_", "ctx_", "intel_"))


def test_encryption_key_required_outside_local() -> None:
    with pytest.raises(ValueError, match="AUTUNE_ENCRYPTION_KEY"):
        Settings(env="staging", secret_key="a-real-key", encryption_key="")


def test_local_tolerates_a_missing_encryption_key() -> None:
    """A deployment that never stores a team credential should still start."""
    assert Settings(env="local", encryption_key="").encryption_key == ""
