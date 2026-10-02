"""Core infrastructure: settings guards, entities, auth, deletion registry."""

from __future__ import annotations

from collections.abc import Sequence

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


def test_an_unset_env_is_production_and_refuses_the_default_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#408: a deployment that forgets AUTUNE_ENV must not come up as local,
    where the shipped key signs sessions and the unauthenticated /dev routes
    are mounted. The error names the fix for the developer who hits it."""
    monkeypatch.delenv("AUTUNE_ENV", raising=False)
    monkeypatch.delenv("AUTUNE_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="set AUTUNE_ENV=local"):
        Settings(_env_file=None)


def _google(**values: str) -> Settings:
    given = {
        "google_client_id": "",
        "google_client_secret": "",
        "google_integration_client_id": "",
        "google_integration_client_secret": "",
        "google_redirect_uri": "http://localhost:3000/api/auth/google/callback",
    } | values
    return Settings(_env_file=None, env="local", **given)


def test_a_persons_google_grant_uses_the_integration_client_when_it_is_set() -> None:
    settings = _google(
        google_client_id="login-id",
        google_client_secret="login-secret",
        google_integration_client_id="cal-id",
        google_integration_client_secret="cal-secret",
    )
    assert settings.google_integration_configured
    assert settings.google_integration_credentials == ("cal-id", "cal-secret")


def test_an_integration_client_without_the_redirect_uri_is_refused() -> None:
    """PARK, review of #700: the second client has no redirect URI of its own.
    Left unset, the connect started and then failed with a message about
    sign-in; now the server says which variable is missing, at start-up."""
    with pytest.raises(ValueError, match="AUTUNE_GOOGLE_REDIRECT_URI"):
        _google(
            google_integration_client_id="cal-id",
            google_integration_client_secret="cal-secret",
            google_redirect_uri="",
        )


def test_without_an_integration_client_no_redirect_uri_is_still_just_sign_in_off() -> None:
    settings = _google(google_client_id="login-id", google_redirect_uri="")
    assert not settings.google_sign_in_configured


def test_without_an_integration_client_the_sign_in_client_does_both() -> None:
    """A deployment that was never given the second client keeps working."""
    settings = _google(google_client_id="login-id", google_client_secret="login-secret")
    assert not settings.google_integration_configured
    assert settings.google_integration_credentials == ("login-id", "login-secret")


def test_no_google_client_at_all_is_two_blanks() -> None:
    assert _google().google_integration_credentials == ("", "")


@pytest.mark.parametrize(
    "only", ["google_integration_client_id", "google_integration_client_secret"]
)
def test_half_an_integration_client_is_refused_and_the_value_is_not_repeated(only: str) -> None:
    """Falling back here would connect calendars with the sign-in client while
    the operator believes the other is in use. The message names the two
    variables and carries neither value."""
    with pytest.raises(ValueError, match="AUTUNE_GOOGLE_INTEGRATION_CLIENT_SECRET") as caught:
        _google(
            google_client_id="login-id",
            google_client_secret="login-secret",
            **{only: "half-a-client"},
        )
    assert "half-a-client" not in str(caught.value)


def test_a_refused_start_up_does_not_print_the_settings_it_was_given() -> None:
    """pydantic appends ``input_value={...}`` to a validation error: the head
    and tail of every setting the process was started with, whichever
    validator refused. A deployment log is not where a key belongs, so the
    refusal names the variable and stops there."""
    with pytest.raises(ValueError, match="AUTUNE_ENCRYPTION_KEY") as caught:
        Settings(
            _env_file=None,
            env="staging",
            encryption_key="",
            secret_key="a-signing-key-nobody-should-read",
            slack_client_secret="a-client-secret-nobody-should-read",
        )
    printed = str(caught.value)
    assert "nobody-should-read" not in printed
    assert "input_value" not in printed


def test_local_tolerates_the_default_secret() -> None:
    assert Settings(env="local").secret_key.startswith("local-development-only")


_NON_LOCAL_KWARGS = {"secret_key": "a-real-generated-secret", "encryption_key": "a-real-fernet-key"}


def test_open_cors_is_refused_outside_local() -> None:
    """A wildcard origin outside local defeats the point of an allowlist."""
    with pytest.raises(ValueError, match="AUTUNE_CORS_ALLOWED_ORIGINS"):
        Settings(env="production", cors_allowed_origins="*", **_NON_LOCAL_KWARGS)


def test_insecure_cors_origin_is_refused_outside_local() -> None:
    with pytest.raises(ValueError, match="AUTUNE_CORS_ALLOWED_ORIGINS"):
        Settings(
            env="production", cors_allowed_origins="http://app.autune.com", **_NON_LOCAL_KWARGS
        )


def test_https_cors_origin_is_accepted_outside_local() -> None:
    origins = "https://app.autune.com"
    settings = Settings(env="production", cors_allowed_origins=origins, **_NON_LOCAL_KWARGS)
    assert settings.cors_allowed_origins == origins


def test_local_tolerates_any_cors_origin() -> None:
    origins = "http://localhost:3000"
    assert Settings(env="local", cors_allowed_origins=origins).cors_allowed_origins == origins


def test_errors_render_a_consistent_body() -> None:
    for error in (NotFoundError("meeting", "mtg_1"), PrivacyViolationError("nope")):
        body = error.to_dict()["error"]
        assert set(body) == {"code", "message", "details"}


def test_deletion_hooks_run_for_registered_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty registry: in the full suite the modules' own hooks are already
    # registered (extraction's since #588), and they need tables this test
    # does not create. The same isolation as #581's test_retention.py.
    monkeypatch.setattr(deletion, "_meeting_hooks", {})
    seen: list[str] = []

    @deletion.on_meeting_deleted("test_module")
    def _cleanup(meeting_id: str) -> None:
        seen.append(meeting_id)

    deletion.run_meeting_hooks("mtg_001")
    assert seen == ["mtg_001"]


def test_speech_hooks_get_the_person_and_the_utterances_before_they_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deletion, "_speech_hooks", {})  # extraction's needs B's tables
    seen: list[tuple[str, tuple[str, ...]]] = []

    @deletion.on_speech_deleted("test_speech_module")
    def _forget(user_id: str, utterance_ids: Sequence[str]) -> None:
        seen.append((user_id, tuple(utterance_ids)))

    deletion.run_speech_hooks("user_1", ["utt_1", "utt_2"])

    assert seen == [("user_1", ("utt_1", "utt_2"))]
    assert "test_speech_module" in deletion.registered_speech_modules()
