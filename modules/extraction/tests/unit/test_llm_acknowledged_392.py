"""A cloud model in module B has to be switched on twice (#392).

"Demo meetings only" and "a paid key for real ones" are rules the code cannot
check. What it can do is refuse to load a configuration that sends speech to a
provider unless the deployment also says it means to:
``AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true``. Proposed by module A's owner
in review of #405.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from autune_extraction import config
from autune_extraction.config import ExtractionSettings

FLAG = "AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392"
SECRET = "sk-live-1234"

CLOUD = [
    {"classifier_impl": "llm"},
    {"classifier_impl": "llm_checked", "classifier_checkpoint": "runs/ckpt"},
    {"resolver_impl": "llm"},
]


def settings(**overrides: Any) -> ExtractionSettings:
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _a_clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell must not decide what these tests see."""
    for name in (
        FLAG,
        "AUTUNE_EXTRACTION_CLASSIFIER_IMPL",
        "AUTUNE_EXTRACTION_RESOLVER_IMPL",
        "AUTUNE_EXTRACTION_LLM_API_KEY",
        "AUTUNE_LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("cloud", CLOUD)
def test_a_cloud_implementation_is_refused_until_it_is_acknowledged(cloud: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match=FLAG):
        settings(llm_api_key="k", **cloud)


@pytest.mark.parametrize("cloud", CLOUD)
def test_acknowledged_it_loads(cloud: dict[str, str]) -> None:
    loaded = settings(llm_api_key="k", llm_acknowledged_392=True, **cloud)

    assert loaded.llm_acknowledged_392 is True


def test_the_refusal_names_which_setting_and_what_it_does() -> None:
    with pytest.raises(ValidationError) as caught:
        settings(resolver_impl="llm")

    message = str(caught.value)
    assert "AUTUNE_EXTRACTION_RESOLVER_IMPL=llm" in message
    assert "cloud model" in message
    assert "#392" in message


def test_the_flag_turns_nothing_on_by_itself() -> None:
    """It is a second switch, never the first."""
    loaded = settings(llm_acknowledged_392=True)

    assert loaded.classifier_impl not in config.CLOUD_IMPLS
    assert loaded.resolver_impl not in config.CLOUD_IMPLS


@pytest.mark.parametrize(
    "local", [{"classifier_impl": "fake"}, {"classifier_impl": "local"}, {"resolver_impl": "fake"}]
)
def test_what_stays_on_our_own_machines_needs_no_acknowledgement(local: dict[str, str]) -> None:
    assert settings(**local).llm_acknowledged_392 is False


def test_it_is_read_from_the_environment_under_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTUNE_EXTRACTION_RESOLVER_IMPL", "llm")
    with pytest.raises(ValidationError, match=FLAG):
        settings()

    monkeypatch.setenv(FLAG, "true")
    assert settings().resolver_impl == "llm"


@pytest.mark.parametrize("env", ["local", "dev", "production"])
def test_no_environment_name_opens_it(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """``AUTUNE_ENV`` defaults to ``local`` (#408): keyed on it, a deployment that
    forgot the variable would be the one let through."""
    monkeypatch.setenv("AUTUNE_ENV", env)

    with pytest.raises(ValidationError, match=FLAG):
        settings(classifier_impl="llm", llm_api_key="k")


def test_the_refusal_prints_no_value_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic's message shows the input it refused unless told not to -- for
    settings, the raw environment, where the provider key is still a plain
    string. Short enough here that it would be printed whole, not elided. The
    message reaches logs and error tracking."""
    monkeypatch.setenv("AUTUNE_EXTRACTION_LLM_API_KEY", SECRET)
    monkeypatch.setenv("AUTUNE_EXTRACTION_RESOLVER_IMPL", "llm")

    with pytest.raises(ValidationError) as caught:
        settings()

    for shown in (str(caught.value), repr(caught.value)):
        assert SECRET not in shown
        assert "input_value" not in shown


def test_get_settings_refuses_too_so_nothing_in_the_module_runs_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTUNE_EXTRACTION_CLASSIFIER_IMPL", "llm")
    monkeypatch.setattr(
        config,
        "ExtractionSettings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match=FLAG):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()
