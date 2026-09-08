"""Core infrastructure: settings guards, entities, auth, deletion registry."""

from __future__ import annotations

import pytest

from autune_core import Base, deletion, issue_token
from autune_core.auth import decode_token
from autune_core.errors import NotFoundError, PermissionDeniedError, PrivacyViolationError
from autune_core.ids import MEETING, has_prefix, new_id
from autune_core.settings import Settings

SHARED_TABLES = {"teams", "users", "team_members", "meetings", "participants", "utterances"}


def test_shared_entities_are_exactly_these_six() -> None:
    """Adding a shared table is a team decision, not a module's to make."""
    assert set(Base.metadata.tables) >= SHARED_TABLES


def test_shared_tables_carry_no_module_prefix() -> None:
    """The absence of a prefix is what marks a table as shared."""
    for name in SHARED_TABLES:
        assert not name.startswith(("aud_", "ext_", "gap_", "ctx_", "intel_"))


def test_utterances_have_no_unmasked_column() -> None:
    """Masking happens before the first write; there is no original to store."""
    columns = set(Base.metadata.tables["utterances"].columns.keys())
    assert not {"raw_text", "unmasked_text", "original_text"} & columns


def test_meeting_deletion_cascades_to_utterances() -> None:
    fk = next(iter(Base.metadata.tables["utterances"].c.meeting_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_ids_are_prefixed() -> None:
    value = new_id(MEETING)
    assert value.startswith("mtg_") and has_prefix(value, MEETING)


def test_token_round_trips() -> None:
    assert decode_token(issue_token("user_001"))["sub"] == "user_001"


def test_invalid_token_is_rejected() -> None:
    with pytest.raises(PermissionDeniedError):
        decode_token("not-a-token")


def test_default_secret_is_refused_outside_local() -> None:
    """A shipped signing key would let anyone forge any session."""
    with pytest.raises(ValueError, match="development default"):
        Settings(env="production", secret_key="local-development-only-change-me")


def test_local_tolerates_the_default_secret() -> None:
    assert Settings(env="local").secret_key.startswith("local-development-only")


def test_errors_render_a_consistent_body() -> None:
    for error in (NotFoundError("meeting", "mtg_1"), PrivacyViolationError("nope")):
        body = error.to_dict()["error"]
        assert set(body) == {"code", "message", "details"}


def test_deletion_hooks_run_for_registered_modules() -> None:
    seen: list[str] = []

    @deletion.on_meeting_deleted("test_module")
    def _cleanup(meeting_id: str) -> None:
        seen.append(meeting_id)

    deletion.run_meeting_hooks("mtg_001")
    assert seen == ["mtg_001"]
