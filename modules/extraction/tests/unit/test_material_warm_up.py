"""The api reads the local material embedder's weights when it starts (#817).

Before this the weights were read inside the first upload or search after
every start. Through the app that one request took 105 s; the web's proxy gave
up on it at 30 s, the screen said the result could not be confirmed, and the
file was stored when the load ended -- with the team's other uploads waiting
on the shelf lock behind it. Two first requests from two teams would each have
read the weights.

``sentence_transformers`` is replaced by a stand-in throughout: these tests are
about when the weights are read and by whom, not about the model.
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from autune_extraction import router as extraction_router
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import MATERIAL_EMBEDDING_DIM
from autune_extraction.pipeline import registry
from autune_extraction.pipeline.embedder import LocalKureEmbedder

CHECKPOINT = "nlpai-lab/KURE-v1"
WARM_UP_THREAD = "extraction-material-embedder-warm-up"
WAIT = 10.0
"""Longer than anything here takes; a wait that runs out fails the test."""


class _Model:
    def encode(self, texts: list[str], **_: Any) -> list[list[float]]:
        return [[0.0] * MATERIAL_EMBEDDING_DIM for _text in texts]


class _Weights:
    """Stands in for ``sentence_transformers``. Counts every read of the
    weights, can hold a read open, and can make one fail."""

    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []
        self.readers: list[str] = []
        self.started = threading.Event()
        self.hold: threading.Event | None = None
        self.fail: Exception | None = None

    def read(self, checkpoint: str, device: str = "cpu") -> _Model:
        self.reads.append((checkpoint, device))
        self.readers.append(threading.current_thread().name)
        self.started.set()
        if self.hold is not None:
            assert self.hold.wait(WAIT), "the test never let the load finish"
        if self.fail is not None:
            raise self.fail
        return _Model()


@pytest.fixture
def weights(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Weights]:
    stand_in = _Weights()
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=stand_in.read),
    )
    yield stand_in
    # Whatever a test left held is let go, so no thread outlives it.
    if stand_in.hold is not None:
        stand_in.hold.set()


def _settings(**overrides: Any) -> ExtractionSettings:
    """Declared defaults and nothing else. Same reason as
    ``test_classifier_seam.settings``."""
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    def apply(**overrides: Any) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: _settings(**overrides))

    # Held by name: a test may put something else in the registry's place, and
    # this fixture is torn down before ``monkeypatch`` puts the real one back.
    cached = registry.get_material_embedder
    cached.cache_clear()
    yield apply
    cached.cache_clear()


def _on(configured: Callable[..., None]) -> None:
    configured(material_upload=True, material_embedder_impl="local")


def _finished(thread: threading.Thread | None) -> None:
    assert thread is not None
    thread.join(WAIT)
    assert not thread.is_alive()


# --- when nothing is started ---------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"material_upload": False, "material_embedder_impl": "local"},
        {
            "material_upload": True,
            "material_embedder_impl": "hosted",
            "material_embedder_endpoint": "https://embedder.autune.internal",
        },
    ],
    ids=["the defaults", "local with uploads off", "uploads on over hosted"],
)
def test_nothing_is_read_unless_uploads_are_on_and_the_embedder_is_local(
    overrides: dict[str, Any],
    configured: Callable[..., None],
    weights: _Weights,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With uploads off about 2 GB would be held for nothing. ``hosted`` keeps
    no weights here, and building it sends its server a question: the embedder
    is not even asked for."""

    asked: list[str] = []

    def counted() -> Any:
        # Counted, not raised: the function under test catches what building
        # an embedder raises, and would hide an assertion with the rest.
        asked.append("the embedder")
        raise RuntimeError("no embedder in this test")

    configured(**overrides)
    monkeypatch.setattr(registry, "get_material_embedder", counted)

    with capture_logs() as logs:
        assert registry.warm_material_embedder() is None

    assert asked == []
    assert logs == []
    assert weights.reads == []


# --- when it is -------------------------------------------------------------------


def test_with_uploads_on_and_local_the_weights_are_read_once_off_the_callers_thread(
    configured: Callable[..., None], weights: _Weights
) -> None:
    _on(configured)

    thread = registry.warm_material_embedder()
    _finished(thread)

    assert thread is not None
    assert thread.daemon
    assert weights.reads == [(CHECKPOINT, "cpu")]
    assert weights.readers == [WARM_UP_THREAD]


def test_the_start_does_not_wait_for_the_load(
    configured: Callable[..., None], weights: _Weights
) -> None:
    """A load that had to download first would otherwise fail the deployment's
    health check before it ended."""
    _on(configured)
    weights.hold = threading.Event()

    thread = registry.warm_material_embedder()

    assert thread is not None
    assert weights.started.wait(WAIT)
    assert thread.is_alive()
    # Built before the start returned, so a request cannot build another.
    assert registry.get_material_embedder.cache_info().currsize == 1
    weights.hold.set()
    _finished(thread)


def test_a_request_during_the_load_waits_for_it_and_reads_nothing_itself(
    configured: Callable[..., None], weights: _Weights
) -> None:
    """The request finds the instance the start built and waits on its load.
    Reading the weights a second time is what cost a second 2 GB."""
    _on(configured)
    weights.hold = threading.Event()
    thread = registry.warm_material_embedder()
    assert weights.started.wait(WAIT)

    answered: list[list[list[float]]] = []
    request = threading.Thread(
        target=lambda: answered.append(registry.get_material_embedder().embed(["환불 기준"]))
    )
    request.start()
    request.join(0.5)

    assert request.is_alive()
    assert len(weights.reads) == 1
    weights.hold.set()
    _finished(thread)
    _finished(request)
    assert len(weights.reads) == 1
    assert len(answered) == 1 and len(answered[0]) == 1


def test_two_first_requests_read_the_weights_once(weights: _Weights) -> None:
    """No start in this one: two requests that both find no model, as two
    teams' first uploads did."""
    embedder = LocalKureEmbedder(CHECKPOINT)
    weights.hold = threading.Event()
    first = threading.Thread(target=lambda: embedder.embed(["첫 번째"]))
    second = threading.Thread(target=lambda: embedder.embed(["두 번째"]))

    first.start()
    assert weights.started.wait(WAIT)
    second.start()
    second.join(0.5)

    assert len(weights.reads) == 1
    weights.hold.set()
    _finished(first)
    _finished(second)
    assert len(weights.reads) == 1


def test_a_load_says_when_it_starts_and_when_it_ends(weights: _Weights) -> None:
    """With only the end in the log, nobody could say how long a load took or
    that one was under way."""
    with capture_logs() as logs:
        LocalKureEmbedder(CHECKPOINT).load()

    assert [(line["event"], line["checkpoint"], line["device"]) for line in logs] == [
        ("extraction_embedder_loading", CHECKPOINT, "cpu"),
        ("extraction_embedder_loaded", CHECKPOINT, "cpu"),
    ]


def test_the_starting_line_is_written_before_the_library_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one time a load was timed in the api, importing the library took
    47 s of its 55 s. A line written after the import would have said the load
    began when it was nearly over."""
    events_at_import: list[list[str]] = []

    class _Library(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            if name != "SentenceTransformer":
                raise AttributeError(name)
            events_at_import.append([line["event"] for line in logs])
            return lambda checkpoint, device="cpu": _Model()

    monkeypatch.setitem(sys.modules, "sentence_transformers", _Library("sentence_transformers"))

    with capture_logs() as logs:
        LocalKureEmbedder(CHECKPOINT).load()

    assert events_at_import == [["extraction_embedder_loading"]]


# --- when the load fails --------------------------------------------------------


def test_a_failed_load_leaves_one_line_naming_the_model_and_the_kind_of_error(
    configured: Callable[..., None], weights: _Weights
) -> None:
    """Not the error's message: a library fills it with paths and addresses."""
    _on(configured)
    weights.fail = OSError("cannot reach https://hub.example/models at /models/huggingface/hub")

    with capture_logs() as logs:
        _finished(registry.warm_material_embedder())

    failures = [line for line in logs if line["log_level"] == "warning"]
    assert failures == [
        {
            "event": "extraction_embedder_warm_up_failed",
            "log_level": "warning",
            "checkpoint": CHECKPOINT,
            "error": "OSError",
        }
    ]


def test_after_a_failed_load_the_next_request_tries_again(
    configured: Callable[..., None], weights: _Weights
) -> None:
    _on(configured)
    weights.fail = OSError("no network")
    _finished(registry.warm_material_embedder())
    weights.fail = None

    vectors = registry.get_material_embedder().embed(["환불 기준"])

    assert len(vectors) == 1
    assert len(weights.reads) == 2


def test_an_embedder_that_cannot_be_built_is_logged_and_the_start_goes_on(
    configured: Callable[..., None], weights: _Weights
) -> None:
    configured(material_upload=True, material_embedder_impl="local", embedder_checkpoint="")

    with capture_logs() as logs:
        assert registry.warm_material_embedder() is None

    assert logs == [
        {
            "event": "extraction_embedder_warm_up_failed",
            "log_level": "warning",
            "checkpoint": "",
            "error": "ValueError",
        }
    ]
    assert weights.reads == []


# --- the api's start ------------------------------------------------------------


def _api(**own: Any) -> FastAPI:
    """Module B's router in an app, the way apps/api includes it."""
    app = FastAPI(**own)
    app.include_router(extraction_router.router, prefix="/api/extraction")
    return app


def _warming() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == WARM_UP_THREAD]


def test_the_api_start_begins_the_load_and_health_answers_during_it(
    configured: Callable[..., None], weights: _Weights
) -> None:
    _on(configured)
    weights.hold = threading.Event()

    with TestClient(_api()) as client:
        assert weights.started.wait(WAIT)
        (thread,) = _warming()
        assert client.get("/api/extraction/health").status_code == 200
        assert thread.is_alive()
        weights.hold.set()
        _finished(thread)

    assert weights.readers == [WARM_UP_THREAD]


def test_an_app_with_a_lifespan_of_its_own_still_begins_the_load(
    configured: Callable[..., None], weights: _Weights
) -> None:
    """apps/api has none today. One added later must not silently drop this."""
    _on(configured)
    seen: list[str] = []

    @asynccontextmanager
    async def own(_app: FastAPI) -> AsyncIterator[None]:
        seen.append("the app's own")
        yield

    with TestClient(_api(lifespan=own)):
        assert weights.started.wait(WAIT)
        for thread in _warming():
            _finished(thread)

    assert seen == ["the app's own"]
    assert len(weights.reads) == 1


def test_with_the_switches_off_the_api_start_reads_nothing(
    configured: Callable[..., None], weights: _Weights
) -> None:
    configured()

    with TestClient(_api()) as client:
        assert client.get("/api/extraction/health").status_code == 200

    assert weights.reads == []
    assert _warming() == []
