"""The acknowledgement is checked when module B starts, not at its first use
(review of #744), and only an explicit true opens it.

The start-up half runs in a fresh interpreter: what is under test is what
importing ``tasks`` and ``router`` does, and a module already imported by this
process cannot be imported again without disturbing every other test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autune_extraction import config
from autune_extraction.config import ExtractionSettings

FLAG = "AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392"
OURS = ("AUTUNE_EXTRACTION_", "AUTUNE_LLM_API_KEY")

DEPLOYED = {
    # What the shared settings ask of a process outside ``local``, so that the
    # only thing left to refuse it is module B's own check.
    "AUTUNE_SECRET_KEY": "not-the-example-key-" + "x" * 32,
    "AUTUNE_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "AUTUNE_CORS_ALLOWED_ORIGINS": "https://autune.example",
}


def settings(**overrides: Any) -> ExtractionSettings:
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _a_clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(OURS):
            monkeypatch.delenv(name, raising=False)


# --- only an explicit true ------------------------------------------------------


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_a_value_that_says_no_does_not_open_it(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """``.env.example`` ships the line as ``=false``; a deployment that copied it
    and set a cloud implementation must still be refused."""
    monkeypatch.setenv(FLAG, value)

    with pytest.raises(ValidationError, match=FLAG):
        settings(resolver_impl="llm", llm_api_key="k")


@pytest.mark.parametrize("value", ["", "maybe", "392"])
def test_a_value_that_is_not_a_yes_or_a_no_does_not_open_it_either(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(FLAG, value)

    with pytest.raises(ValidationError):
        settings(resolver_impl="llm", llm_api_key="k")


@pytest.mark.parametrize("value", ["true", "True", "1"])
def test_an_explicit_yes_opens_it(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(FLAG, value)

    assert settings(resolver_impl="llm", llm_api_key="k").llm_acknowledged_392 is True


# --- at start-up ----------------------------------------------------------------


def test_require_loadable_raises_what_loading_would(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_EXTRACTION_RESOLVER_IMPL", "llm")
    monkeypatch.setattr(
        config,
        "ExtractionSettings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )

    with pytest.raises(ValidationError, match=FLAG):
        config.require_loadable()


def test_require_loadable_leaves_the_settings_cache_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Priming ``get_settings`` at import would pin the environment of that
    moment for every later caller."""
    config.get_settings.cache_clear()
    monkeypatch.setattr(
        config,
        "ExtractionSettings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )

    config.require_loadable()

    assert config.get_settings.cache_info().currsize == 0


def importing(module: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Import one of module B's entry points in a fresh interpreter, in a
    directory with no ``.env``, with only ``env`` set of B's variables."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(OURS)}
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=tmp_path,
        env={**clean, "AUTUNE_ENV": "local", **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("module", ["autune_extraction.tasks", "autune_extraction.router"])
@pytest.mark.parametrize("env", ["local", "production"])
def test_a_process_given_an_unacknowledged_cloud_model_does_not_start(
    module: str, env: str, tmp_path: Path
) -> None:
    """The worker imports ``tasks`` and the API imports ``router``. Neither gets
    as far as serving anything.

    In both environments, because with ``AUTUNE_ENV=local`` the router already
    read B's settings at import for another reason (its dev routes), and a test
    run only there passes whether or not anything checks at start-up."""
    done = importing(
        module,
        tmp_path,
        AUTUNE_ENV=env,
        **DEPLOYED,
        AUTUNE_EXTRACTION_RESOLVER_IMPL="llm",
        AUTUNE_EXTRACTION_LLM_API_KEY="sk-live-1234",
    )

    assert done.returncode != 0
    assert FLAG in done.stderr, done.stderr[-600:]
    assert "sk-live-1234" not in done.stderr


@pytest.mark.parametrize("module", ["autune_extraction.tasks", "autune_extraction.router"])
def test_acknowledged_or_not_using_one_it_starts(module: str, tmp_path: Path) -> None:
    acknowledged = importing(
        module,
        tmp_path,
        AUTUNE_EXTRACTION_RESOLVER_IMPL="llm",
        AUTUNE_EXTRACTION_LLM_API_KEY="k",
        **{FLAG: "true"},
    )
    plain = importing(module, tmp_path)
    deployed = importing(module, tmp_path, AUTUNE_ENV="production", **DEPLOYED)

    assert acknowledged.returncode == 0, acknowledged.stderr[-400:]
    assert plain.returncode == 0, plain.stderr[-400:]
    # The same settings the refusal was tested with start cleanly without a
    # cloud model, so that test's failure was B's and nothing else's.
    assert deployed.returncode == 0, deployed.stderr[-400:]
