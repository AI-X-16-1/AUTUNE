"""Uploads switched on need an embedder that is a model (#817).

``AUTUNE_EXTRACTION_MATERIAL_UPLOAD=true`` with
``AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL`` left at its default, ``fake``,
looked on in every way -- the rules said enabled, an upload was stored and
listed -- while every vector was the fake's: a search found by shared
characters, and what was uploaded then would never be found once the embedder
became a model, because a material is found only by the embedder it was stored
with. Module B's settings now refuse to load that pair
(``ExtractionSettings._uploads_are_kept_with_a_model``), and
``require_loadable`` makes the refusal a refusal to start.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autune_extraction import config
from autune_extraction.config import ExtractionSettings

UPLOAD = "AUTUNE_EXTRACTION_MATERIAL_UPLOAD"
EMBEDDER = "AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL"


@pytest.fixture(autouse=True)
def _nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the shell running the tests has set is not what is tested."""
    for name in (UPLOAD, EMBEDDER, "AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


def settings(**overrides: object) -> ExtractionSettings:
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg, arg-type]


def test_uploads_on_over_the_fake_embedder_do_not_load() -> None:
    with pytest.raises(ValidationError) as refused:
        settings(material_upload=True)

    said = str(refused.value)
    assert UPLOAD in said and f"{EMBEDDER}=fake" in said
    assert "local or hosted" in said, "it says what to set"


def test_the_fake_embedder_named_outright_is_refused_the_same() -> None:
    with pytest.raises(ValidationError, match=EMBEDDER):
        settings(material_upload=True, material_embedder_impl="fake")


@pytest.mark.parametrize("impl", ["local", "hosted"])
def test_uploads_on_over_a_model_load(impl: str) -> None:
    loaded = settings(material_upload=True, material_embedder_impl=impl)

    assert loaded.material_upload is True and loaded.material_embedder_impl == impl


def test_the_defaults_load() -> None:
    """Uploads off, the embedder ``fake``: what ``.env.example`` ships and
    what every process has run on."""
    loaded = settings()

    assert loaded.material_upload is False and loaded.material_embedder_impl == "fake"


def test_the_fake_embedder_named_with_uploads_off_loads() -> None:
    assert settings(material_upload=False, material_embedder_impl="fake").material_upload is False


@pytest.mark.parametrize("value", ["true", "True", "1"])
def test_the_switch_read_from_the_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(UPLOAD, value)

    with pytest.raises(ValidationError, match=EMBEDDER):
        settings()

    monkeypatch.setenv(EMBEDDER, "local")
    assert settings().material_upload is True


@pytest.mark.parametrize("env", ["local", "dev", "staging", "production"])
def test_no_environment_is_let_through(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """``local`` is what ``.env.example`` ships, so a deployment made from
    that file is ``local`` too: the check does not read the name."""
    monkeypatch.setenv("AUTUNE_ENV", env)
    monkeypatch.setenv(UPLOAD, "true")

    with pytest.raises(ValidationError, match=EMBEDDER):
        settings()


@pytest.mark.parametrize(
    "overrides",
    [
        {"material_embedder_impl": "hosted", "material_embedder_endpoint": ""},
        {"material_embedder_impl": "local", "embedder_checkpoint": ""},
        {"material_embedder_impl": "no-such-embedder"},
    ],
    ids=["hosted without an endpoint", "a model without a checkpoint", "an unknown name"],
)
def test_what_the_registry_refuses_is_left_to_the_registry(overrides: dict[str, object]) -> None:
    """Each of these fails at the first upload or search, before a vector is
    made (``registry.get_material_embedder``). Only the fake works quietly,
    so only the fake is refused here."""
    assert settings(material_upload=True, **overrides).material_upload is True


def test_switching_uploads_on_in_loaded_settings_is_not_refused() -> None:
    """How the upload tests turn the switch on: on settings already loaded.
    A validator runs when settings load, so those tests keep the fake."""
    loaded = settings()

    loaded.material_upload = True

    assert loaded.material_embedder_impl == "fake"


def test_a_process_set_so_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """``require_loadable`` runs when ``tasks`` is imported, by the worker and,
    through the router, by the API."""
    monkeypatch.setenv(UPLOAD, "true")
    monkeypatch.setattr(
        config,
        "ExtractionSettings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )

    with pytest.raises(ValidationError, match=EMBEDDER):
        config.require_loadable()

    monkeypatch.setenv(EMBEDDER, "hosted")
    config.require_loadable()
