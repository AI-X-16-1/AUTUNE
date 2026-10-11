"""``GET /cloud-model``: whether this server sends meeting text to a cloud model.

The screens that take a recording in show #392's operating rule only on a
server the rule is about, and this one boolean is how they know. It is the
same list of switches the start-up refusal walks
(``test_llm_acknowledged_392``), and the answer carries nothing else.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autune_core import User
from autune_core.auth import current_user
from autune_extraction import router as router_module
from autune_extraction.config import ExtractionSettings
from autune_extraction.router import router

from .conftest import READER

PREFIX = "/api/extraction"
SECRET = "sk-live-1234"

CLOUD = [
    {"classifier_impl": "llm"},
    {"classifier_impl": "llm_checked", "classifier_checkpoint": "runs/ckpt"},
    {"resolver_impl": "llm"},
    {"summary_impl": "llm"},
    {"title_impl": "llm"},
    {"nli_impl": "llm"},
]


def settings(**overrides: Any) -> ExtractionSettings:
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _a_clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell must not decide what these tests see."""
    for name in (
        "AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392",
        "AUTUNE_EXTRACTION_CLASSIFIER_IMPL",
        "AUTUNE_EXTRACTION_RESOLVER_IMPL",
        "AUTUNE_EXTRACTION_SUMMARY_IMPL",
        "AUTUNE_EXTRACTION_TITLE_IMPL",
        "AUTUNE_EXTRACTION_NLI_IMPL",
        "AUTUNE_EXTRACTION_LLM_API_KEY",
        "AUTUNE_LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def client(monkeypatch: pytest.MonkeyPatch, loaded: ExtractionSettings) -> TestClient:
    """Module B's router on a bare app, signed in; the route reads no row."""
    monkeypatch.setattr(router_module, "get_settings", lambda: loaded)
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    reader = User(id=READER, email=f"{READER}@example.com", display_name="읽는 사람")
    app.dependency_overrides[current_user] = lambda: reader
    return TestClient(app)


def test_a_server_on_its_defaults_sends_nothing_out(monkeypatch: pytest.MonkeyPatch) -> None:
    response = client(monkeypatch, settings()).get(f"{PREFIX}/cloud-model")

    assert response.status_code == 200
    assert response.json() == {"in_use": False}


@pytest.mark.parametrize("cloud", CLOUD)
def test_any_one_cloud_implementation_makes_it_true(
    monkeypatch: pytest.MonkeyPatch, cloud: dict[str, str]
) -> None:
    loaded = settings(llm_api_key=SECRET, llm_acknowledged_392=True, **cloud)

    response = client(monkeypatch, loaded).get(f"{PREFIX}/cloud-model")

    assert response.json() == {"in_use": True}


def test_the_acknowledgement_alone_is_not_a_cloud_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag turns nothing on, so a server that set only it sends nothing."""
    loaded = settings(llm_acknowledged_392=True)

    assert client(monkeypatch, loaded).get(f"{PREFIX}/cloud-model").json() == {"in_use": False}


def test_the_answer_is_the_one_boolean_and_nothing_about_the_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which switch, which model and whether a key is set stay on the server."""
    loaded = settings(
        llm_api_key=SECRET, llm_acknowledged_392=True, classifier_impl="llm", nli_impl="llm"
    )

    response = client(monkeypatch, loaded).get(f"{PREFIX}/cloud-model")

    assert list(response.json()) == ["in_use"]
    for kept in (SECRET, loaded.llm_model, loaded.nli_model, "classifier", "nli"):
        assert kept not in response.text


def test_every_switch_the_refusal_walks_is_one_the_answer_counts() -> None:
    """One list behind both: a switch that needs the acknowledgement and did not
    turn the notice on would be a server the rule is about, saying nothing."""
    for cloud in CLOUD:
        with pytest.raises(ValueError, match="ACKNOWLEDGED_392"):
            settings(llm_api_key=SECRET, **cloud)
        assert settings(
            llm_api_key=SECRET, llm_acknowledged_392=True, **cloud
        ).sends_meeting_text_out
