"""The hosted material embedder (#817): KURE-v1 on our own inference server.

Driven through the real ``autune_integrations.HttpClient`` over an
``httpx.MockTransport``, so the error mapping and the outbound guard under test
are the ones a deployment runs, not a stand-in for them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from autune_extraction.config import ExtractionSettings
from autune_extraction.models import MATERIAL_EMBEDDING_DIM
from autune_extraction.pipeline import FakeEmbedder, embedder, registry
from autune_extraction.pipeline.embedder import HostedKureEmbedder
from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

ENDPOINT = "http://inference.invalid"
QUESTION = "출시 일정 [이름] 담당 범위"
"""Masked, as every caller hands it over: the masker has already replaced the
name, so this is what the server is allowed to see."""


def _unit(dim: int) -> list[float]:
    return [1.0] + [0.0] * (dim - 1)


def _hosted(
    handler: Callable[[httpx.Request], httpx.Response], *, dim: int = MATERIAL_EMBEDDING_DIM
) -> HostedKureEmbedder:
    client = HostedKureEmbedder(ENDPOINT, "ckpt", dim=dim)
    client._client._client = httpx.Client(base_url=ENDPOINT, transport=httpx.MockTransport(handler))
    return client


def _answering(width: int, seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        texts = json.loads(request.content)["texts"]
        return httpx.Response(200, json={"embeddings": [_unit(width) for _ in texts]})

    return handler


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the re-attempts, drop the sleeps between them."""
    monkeypatch.setattr(embedder, "RETRY_BACKOFF_SEC", (0.0, 0.0))


# --- the client ------------------------------------------------------------------


def test_the_servers_vectors_come_back_one_per_text_in_order() -> None:
    seen: list[httpx.Request] = []

    vectors = _hosted(_answering(MATERIAL_EMBEDDING_DIM, seen)).embed([QUESTION, "둘째 조각"])

    assert len(vectors) == 2
    assert all(len(v) == MATERIAL_EMBEDDING_DIM for v in vectors)
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/embed"
    assert json.loads(request.content) == {"texts": [QUESTION, "둘째 조각"]}


def test_it_names_the_checkpoint_it_was_given() -> None:
    assert _hosted(_answering(MATERIAL_EMBEDDING_DIM, [])).model_version == "ckpt"


def test_an_empty_batch_sends_no_request() -> None:
    seen: list[httpx.Request] = []

    assert _hosted(_answering(MATERIAL_EMBEDDING_DIM, seen)).embed([]) == []
    assert seen == []


def test_a_long_upload_is_split_to_fit_the_outbound_guard() -> None:
    seen: list[httpx.Request] = []
    pieces = ["가" * 500] * 10  # 5,000 characters, over the guard's 4,000

    vectors = _hosted(_answering(MATERIAL_EMBEDDING_DIM, seen)).embed(pieces)

    assert len(vectors) == 10
    assert len(seen) == 2
    for request in seen:
        assert sum(len(t) for t in json.loads(request.content)["texts"]) <= MAX_OUTBOUND_CHARS


def test_a_vector_of_another_width_is_refused() -> None:
    """A server running some other model would put vectors in the column that
    nothing stored there is comparable with -- or that cannot be stored."""
    with pytest.raises(ValueError, match=f"width 768, not {MATERIAL_EMBEDDING_DIM}"):
        _hosted(_answering(768, [])).embed([QUESTION])


def test_a_server_answering_the_wrong_count_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [_unit(MATERIAL_EMBEDDING_DIM)]})

    with pytest.raises(ValueError, match="asked for 2 embeddings"):
        _hosted(handler).embed([QUESTION, "둘째 조각"])


def test_a_server_error_surfaces_as_transient_after_the_re_attempts() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(TransientIntegrationError):
        _hosted(handler).embed([QUESTION])
    assert len(calls) == 3


def test_one_bad_second_is_ridden_out() -> None:
    calls: list[httpx.Request] = []
    good = _answering(MATERIAL_EMBEDDING_DIM, [])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(502) if len(calls) == 1 else good(request)

    assert len(_hosted(handler).embed([QUESTION])) == 1
    assert len(calls) == 2


def test_a_refused_request_is_permanent_and_not_repeated() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(422, json={"detail": QUESTION})

    with pytest.raises(PermanentIntegrationError) as raised:
        _hosted(handler).embed([QUESTION])
    assert len(calls) == 1
    assert QUESTION not in str(raised.value)


def test_no_text_reaches_a_log_line() -> None:
    calls: list[httpx.Request] = []
    good = _answering(MATERIAL_EMBEDDING_DIM, [])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500) if len(calls) == 1 else good(request)

    with capture_logs() as logs:
        _hosted(handler).embed([QUESTION])

    assert any(entry["event"] == "extraction_embedder_retry" for entry in logs)
    for entry in logs:
        assert QUESTION not in repr(entry)
        assert "출시" not in repr(entry)


# --- the registry ----------------------------------------------------------------


def _settings(**overrides: Any) -> ExtractionSettings:
    """Declared defaults and nothing else -- see ``test_embedder_seam._settings``."""
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    def apply(**overrides: Any) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: _settings(**overrides))

    registry.get_material_embedder.cache_clear()
    yield apply
    registry.get_material_embedder.cache_clear()


def test_the_default_material_embedder_is_the_wide_fake(configured: Callable[..., None]) -> None:
    configured()

    model = registry.get_material_embedder()

    assert isinstance(model, FakeEmbedder)
    assert len(model.embed(["a"])[0]) == MATERIAL_EMBEDDING_DIM


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The server behind the endpoint the registry is given: ``info`` is what
    its ``/info`` answers (an ``httpx.Response`` to answer that instead),
    ``seen`` every request it got."""
    state: dict[str, Any] = {
        "info": {"model_version": "ckpt", "dim": MATERIAL_EMBEDDING_DIM},
        "seen": [],
    }
    real = embedder._embedder_client

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        if request.url.path == "/info":
            answer = state["info"]
            return (
                answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)
            )
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200, json={"embeddings": [_unit(MATERIAL_EMBEDDING_DIM) for _ in texts]}
        )

    def client(endpoint: str) -> Any:
        made = real(endpoint)
        made._client = httpx.Client(base_url=endpoint, transport=httpx.MockTransport(handler))
        return made

    monkeypatch.setattr(embedder, "_embedder_client", client)
    return state


def _hosted_settings(configured: Callable[..., None]) -> None:
    configured(
        material_embedder_impl="hosted",
        material_embedder_endpoint="http://embed.internal:8080",
        embedder_checkpoint="ckpt",
    )


def _paths(server: dict[str, Any]) -> list[tuple[str, str]]:
    return [(request.method, request.url.path) for request in server["seen"]]


def test_hosted_is_built_once_its_server_names_the_checkpoint(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    _hosted_settings(configured)

    model = registry.get_material_embedder()

    assert isinstance(model, HostedKureEmbedder)
    assert model.model_version == "ckpt"
    assert _paths(server) == [("GET", "/info")]
    assert server["seen"][0].content == b""


def test_the_server_is_asked_once_a_process_and_then_embeds(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    _hosted_settings(configured)

    registry.get_material_embedder()
    vectors = registry.get_material_embedder().embed([QUESTION])

    assert len(vectors) == 1 and len(vectors[0]) == MATERIAL_EMBEDDING_DIM
    assert _paths(server) == [("GET", "/info"), ("POST", "/embed")]


def test_a_server_running_another_model_of_the_same_width_is_refused_before_any_text(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    """What the width check cannot see: bge-m3 answers 1024 as KURE-v1 does."""
    _hosted_settings(configured)
    server["info"] = {"model_version": "BAAI/bge-m3", "dim": MATERIAL_EMBEDDING_DIM}

    with pytest.raises(ValueError, match="BAAI/bge-m3") as refused:
        registry.get_material_embedder()

    assert "'ckpt'" in str(refused.value)
    assert _paths(server) == [("GET", "/info")]


@pytest.mark.parametrize(
    "info",
    [
        {"dim": MATERIAL_EMBEDDING_DIM},
        {"model_version": "", "dim": MATERIAL_EMBEDDING_DIM},
        {"model_version": None, "dim": MATERIAL_EMBEDDING_DIM},
        {"model_version": ["ckpt"], "dim": MATERIAL_EMBEDDING_DIM},
        {},
    ],
    ids=["no-name", "empty-name", "null-name", "name-in-a-list", "empty-answer"],
)
def test_a_server_that_names_no_model_is_refused_not_assumed(
    configured: Callable[..., None], server: dict[str, Any], info: dict[str, Any]
) -> None:
    _hosted_settings(configured)
    server["info"] = info

    with pytest.raises(ValueError, match="names no model_version"):
        registry.get_material_embedder()

    assert _paths(server) == [("GET", "/info")]


@pytest.mark.parametrize(
    "named",
    ["CKPT", "ckpt ", "ckpt@main", "org/ckpt"],
    ids=["another-case", "trailing-space", "with-a-revision", "with-an-owner"],
)
def test_the_name_is_compared_string_for_string(
    configured: Callable[..., None], server: dict[str, Any], named: str
) -> None:
    _hosted_settings(configured)
    server["info"] = {"model_version": named, "dim": MATERIAL_EMBEDDING_DIM}

    with pytest.raises(ValueError, match="under exactly that name"):
        registry.get_material_embedder()


@pytest.mark.parametrize("dim", [768, "1024", True], ids=["narrower", "as-text", "a-boolean"])
def test_a_server_stating_another_width_is_refused(
    configured: Callable[..., None], server: dict[str, Any], dim: Any
) -> None:
    _hosted_settings(configured)
    server["info"] = {"model_version": "ckpt", "dim": dim}

    with pytest.raises(ValueError, match="states a width"):
        registry.get_material_embedder()

    assert _paths(server) == [("GET", "/info")]


def test_a_server_that_states_no_width_is_held_to_its_name_and_to_each_vector(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    _hosted_settings(configured)
    server["info"] = {"model_version": "ckpt"}

    assert isinstance(registry.get_material_embedder(), HostedKureEmbedder)


def test_a_refusal_is_not_remembered_so_a_corrected_server_is_asked_again(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    _hosted_settings(configured)
    server["info"] = {"model_version": "another", "dim": MATERIAL_EMBEDDING_DIM}
    with pytest.raises(ValueError):
        registry.get_material_embedder()

    server["info"] = {"model_version": "ckpt", "dim": MATERIAL_EMBEDDING_DIM}

    assert isinstance(registry.get_material_embedder(), HostedKureEmbedder)
    assert _paths(server) == [("GET", "/info"), ("GET", "/info")]


def test_a_server_that_cannot_be_asked_builds_nothing(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    _hosted_settings(configured)
    server["info"] = httpx.Response(503)

    with pytest.raises(TransientIntegrationError):
        registry.get_material_embedder()

    server["info"] = httpx.Response(404)
    with pytest.raises(PermanentIntegrationError):
        registry.get_material_embedder()

    assert ("POST", "/embed") not in _paths(server)


def test_what_a_server_calls_itself_is_cut_short_in_the_error(
    configured: Callable[..., None], server: dict[str, Any]
) -> None:
    _hosted_settings(configured)
    server["info"] = {"model_version": "x" * 5000, "dim": MATERIAL_EMBEDDING_DIM}

    with pytest.raises(ValueError) as refused:
        registry.get_material_embedder()

    assert len(str(refused.value)) < 400


def test_built_by_hand_the_client_asks_nothing_until_told_to() -> None:
    """The constructor makes no request; ``confirm_model`` is the registry's
    step, and these tests' other clients are built without it."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"model_version": "ckpt", "dim": MATERIAL_EMBEDDING_DIM})

    client = _hosted(handler)
    assert seen == []

    client.confirm_model()

    assert [(r.method, r.url.path) for r in seen] == [("GET", "/info")]


def test_hosted_without_an_endpoint_is_refused(configured: Callable[..., None]) -> None:
    configured(material_embedder_impl="hosted")

    with pytest.raises(ValueError, match="AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_ENDPOINT"):
        registry.get_material_embedder()


def test_hosted_without_a_checkpoint_is_refused(configured: Callable[..., None]) -> None:
    configured(
        material_embedder_impl="hosted",
        material_embedder_endpoint="http://embed.internal:8080",
        embedder_checkpoint="",
    )

    with pytest.raises(ValueError, match="AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT"):
        registry.get_material_embedder()


def test_an_unknown_material_embedder_is_refused_naming_hosted(
    configured: Callable[..., None],
) -> None:
    configured(material_embedder_impl="cloud")

    with pytest.raises(ValueError, match="'hosted'"):
        registry.get_material_embedder()
