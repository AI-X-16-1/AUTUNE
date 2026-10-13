"""The embedder and NLI clients reach our inference servers through the outbound
guard: every body is checked, and batches are split to fit its size cap."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import autune_integrations.base
from autune_context.config import get_settings
from autune_context.constants import EMBEDDING_DIM
from autune_context.pipeline._serving import batches, clip
from autune_context.pipeline.embedding import KureHttpEmbedder
from autune_context.pipeline.nli import KlueKorNliHttp
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, PrivacyViolationError


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every POST body the inference server got. ``/health`` answers plain
    text, as a server may; ``/info`` answers JSON."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, text="ok")
        if request.url.path == "/info":
            return httpx.Response(200, json={"model_version": "m", "dim": EMBEDDING_DIM})
        body = json.loads(request.content)
        seen.append(body)
        if request.url.path == "/embed":
            return httpx.Response(200, json={"embeddings": [[0.0] * 3 for _ in body["texts"]]})
        scores = {"entailment": 0.1, "contradiction": 0.2, "neutral": 0.7}
        return httpx.Response(200, json={"results": [scores for _ in body["pairs"]]})

    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(autune_integrations.base.httpx, "Client", client)
    return seen


def _chars(body: dict[str, Any]) -> int:
    values = body.get("texts") or [side for pair in body["pairs"] for side in pair]
    return sum(len(v) for v in values)


def test_the_embedder_splits_texts_into_requests_the_guard_lets_through(server) -> None:
    texts = ["가" * 1500 for _ in range(7)]

    vectors = KureHttpEmbedder(get_settings()).embed(texts)

    assert len(vectors) == 7
    assert len(server) > 1
    assert all(_chars(body) <= MAX_OUTBOUND_CHARS for body in server)
    assert [t for body in server for t in body["texts"]] == texts


def test_the_embedder_cuts_a_text_no_request_could_carry(server) -> None:
    KureHttpEmbedder(get_settings()).embed(["나" * (MAX_OUTBOUND_CHARS + 500)])

    assert [len(t) for t in server[0]["texts"]] == [MAX_OUTBOUND_CHARS]


def test_the_embedder_refuses_unmasked_personal_data_before_sending(server) -> None:
    with pytest.raises(PrivacyViolationError):
        KureHttpEmbedder(get_settings()).embed(["연락처는 010-1234-5678 입니다"])

    assert server == []


def test_nli_splits_pairs_into_requests_the_guard_lets_through(server) -> None:
    pairs = [("다" * 900, "라" * 900) for _ in range(5)]

    results = KlueKorNliHttp(get_settings()).classify(pairs)

    assert [r.label for r in results] == ["neutral"] * 5
    assert len(server) > 1
    assert all(_chars(body) <= MAX_OUTBOUND_CHARS for body in server)


def test_nli_cuts_each_side_to_half_the_cap(server) -> None:
    KlueKorNliHttp(get_settings()).classify([("마" * MAX_OUTBOUND_CHARS, "바")])

    ((premise, hypothesis),) = server[0]["pairs"]
    assert (len(premise), hypothesis) == (MAX_OUTBOUND_CHARS // 2, "바")


def test_batches_keep_order_and_never_pass_the_budget() -> None:
    assert list(batches(["aa", "bbb", "c", "dddd"], len, budget=4)) == [
        ["aa"],
        ["bbb", "c"],
        ["dddd"],
    ]
    assert clip("abcdef", 4) == "abcd"
