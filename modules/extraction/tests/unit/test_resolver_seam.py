"""The reference resolver seam (#175): what a resolution promises, and the
three implementations behind ``AUTUNE_EXTRACTION_RESOLVER_IMPL``.

No weights, no network for ``local``'s load path here -- see
``test_action_item_drafts.py`` for how ``FakeResolver`` plugs into the rest of
step 3. This file is the seam itself: the fake's contract, the hosted client's
retry and fallback behaviour, and the groundedness check both models are
validated against.
"""

from __future__ import annotations

from typing import Any

import pytest

from autune_extraction.config import ExtractionSettings
from autune_extraction.pipeline import FakeResolver, ResolutionRequest, registry
from autune_extraction.pipeline.resolver import (
    MAX_CONTEXT_UTTERANCES,
    HostedResolver,
    LocalQwenResolver,
    _grounded,
    _window_text,
)
from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError

# --- the fake: what runs before a resolver model exists ------------------------


def test_the_fake_returns_the_target_unchanged() -> None:
    requests = [
        ResolutionRequest(target="그거 제가 할게요", context=("회의실 예약해야 해요",)),
        ResolutionRequest(target="네 알겠습니다"),
    ]

    resolved = FakeResolver().resolve(requests)

    assert resolved == ["그거 제가 할게요", "네 알겠습니다"]


def test_the_fake_names_itself() -> None:
    assert FakeResolver().model_version == "fake"


def test_an_empty_batch_is_not_an_error() -> None:
    assert FakeResolver().resolve([]) == []


# --- what a request carries ------------------------------------------------


def test_context_defaults_to_empty() -> None:
    assert ResolutionRequest(target="네").context == ()


# --- groundedness: no new numbers ------------------------------------------


def test_a_number_present_in_the_window_is_grounded() -> None:
    request = ResolutionRequest(target="3시에 봅시다", context=("오늘 3시 어때요",))
    assert _grounded("3시에 봅시다", _window_text(request))


def test_a_number_absent_from_the_window_is_not_grounded() -> None:
    request = ResolutionRequest(target="그때 봅시다", context=("내일 어때요",))
    assert not _grounded("5시에 봅시다", _window_text(request))


def test_text_with_no_numbers_is_trivially_grounded() -> None:
    request = ResolutionRequest(target="그거 할게요", context=("회의실 예약해야죠",))
    assert _grounded("회의실 예약 할게요", _window_text(request))


# --- groundedness: no substituted names (#366) ------------------------------


def test_a_named_person_present_in_the_window_is_grounded() -> None:
    request = ResolutionRequest(target="그거 제가 할게요", context=("박지영님이 부탁하신 거요",))
    assert _grounded("박지영님이 부탁한 거 제가 할게요", _window_text(request))


def test_a_named_person_absent_from_the_window_is_not_grounded() -> None:
    request = ResolutionRequest(target="그거 제가 할게요", context=("팀에서 부탁한 거요",))
    assert not _grounded("박지영님이 부탁한 거 제가 할게요", _window_text(request))


def test_the_window_is_context_then_target_in_order() -> None:
    request = ResolutionRequest(target="target", context=("first", "second"))
    assert _window_text(request) == "first\nsecond\ntarget"


# --- the local resolver's own guards ----------------------------------------


def test_a_local_resolver_without_a_checkpoint_is_refused() -> None:
    with pytest.raises(ValueError, match="checkpoint"):
        LocalQwenResolver("")


def test_the_local_resolver_records_its_checkpoint_as_its_version() -> None:
    resolver = LocalQwenResolver("Qwen/Qwen3-4B-Instruct-2507")
    assert resolver.model_version == "Qwen/Qwen3-4B-Instruct-2507"


def test_asking_the_local_resolver_for_cuda_without_it_fails_before_loading() -> None:
    resolver = LocalQwenResolver("Qwen/Qwen3-4B-Instruct-2507", device="cuda")
    import importlib.util

    if importlib.util.find_spec("torch") is None:
        pytest.skip("torch is not installed here")
    import torch

    if torch.cuda.is_available():
        pytest.skip("this machine has CUDA; the refusal path cannot fire")

    with pytest.raises(RuntimeError, match="no CUDA"):
        resolver._load()


# --- the registry: config string -> implementation --------------------------


def _settings(**overrides: Any) -> ExtractionSettings:
    """Declared defaults and nothing else -- ``model_config`` carries
    ``env_file=".env"``, so a plain ``ExtractionSettings()`` would read whatever
    the developer running the suite happens to have exported. Same reason as
    ``test_classifier_seam.settings``."""
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    def apply(**overrides: Any) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: _settings(**overrides))

    registry.get_resolver.cache_clear()
    yield apply
    registry.get_resolver.cache_clear()


def test_the_default_resolver_is_the_fake(configured) -> None:
    configured()
    assert isinstance(registry.get_resolver(), FakeResolver)


def test_local_without_a_checkpoint_is_refused(configured) -> None:
    configured(resolver_impl="local")
    with pytest.raises(ValueError, match="RESOLVER_CHECKPOINT"):
        registry.get_resolver()


def test_hosted_without_an_endpoint_is_refused(configured) -> None:
    configured(resolver_impl="hosted", resolver_checkpoint="ckpt")
    with pytest.raises(ValueError, match="RESOLVER_ENDPOINT"):
        registry.get_resolver()


def test_hosted_with_a_checkpoint_and_endpoint_builds(configured) -> None:
    configured(resolver_impl="hosted", resolver_checkpoint="ckpt", resolver_endpoint="http://x")
    resolver = registry.get_resolver()
    assert isinstance(resolver, HostedResolver)
    assert resolver.model_version == "ckpt"


def test_an_unknown_impl_is_refused(configured) -> None:
    configured(resolver_impl="external")
    with pytest.raises(ValueError, match="unknown"):
        registry.get_resolver()


# --- the hosted resolver: retry and fall back, never raise for one request --


class Server:
    """Same shape as ``test_classifier_seam.Server``: raised failures come off
    the front of the queue, one per call, before any answer is given."""

    def __init__(self, *failures: Exception, answer: str = "회의실 예약 제가 할게요") -> None:
        self.failures = list(failures)
        self.calls = 0
        self._answer = answer

    def request(self, method: str, path: str, *, json: dict) -> dict:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"resolved": self._answer}


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch):
    def build(server: Server) -> HostedResolver:
        resolver = HostedResolver("http://inference.invalid", "ckpt")
        resolver._client = server  # type: ignore[assignment]
        return resolver

    from autune_extraction.pipeline import resolver as resolver_module

    monkeypatch.setattr(resolver_module.time, "sleep", lambda _seconds: None)
    return build


def test_a_transient_failure_is_retried_then_answered(hosted) -> None:
    server = Server(TransientIntegrationError("extraction-resolver returned 502"))

    resolved = hosted(server).resolve([ResolutionRequest(target="그거 제가 할게요")])

    assert resolved == ["회의실 예약 제가 할게요"]
    assert server.calls == 2


def test_a_server_that_never_recovers_falls_back_to_the_target_rather_than_raising(
    hosted,
) -> None:
    """#175's own failure rule: a call that never succeeds still returns the
    request's own text, never an exception -- the pipeline does not stop for
    one commitment."""
    server = Server(*[TransientIntegrationError("timed out") for _ in range(5)])

    resolved = hosted(server).resolve([ResolutionRequest(target="그거 제가 할게요")])

    assert resolved == ["그거 제가 할게요"]


def test_a_rejected_request_also_falls_back_rather_than_raising(hosted) -> None:
    """Unlike the classifier, a resolver's own failure contract says any
    failure -- rejection included -- degrades to the raw quote."""
    server = Server(PermanentIntegrationError("extraction-resolver rejected the request"))

    resolved = hosted(server).resolve([ResolutionRequest(target="그거 제가 할게요")])

    assert resolved == ["그거 제가 할게요"]


def test_an_ungrounded_answer_falls_back_to_the_target(hosted) -> None:
    """The server answers, but invents a time nothing in the window said."""
    server = Server(answer="5시에 회의실 예약 할게요")

    resolved = hosted(server).resolve(
        [ResolutionRequest(target="그거 제가 할게요", context=("회의실 예약해야죠",))]
    )

    assert resolved == ["그거 제가 할게요"]


def test_a_grounded_answer_is_kept(hosted) -> None:
    server = Server(answer="회의실 예약 제가 할게요")

    resolved = hosted(server).resolve(
        [ResolutionRequest(target="그거 제가 할게요", context=("회의실 예약해야죠",))]
    )

    assert resolved == ["회의실 예약 제가 할게요"]


def test_several_requests_stay_in_order(hosted) -> None:
    server = Server(answer="답")
    requests = [ResolutionRequest(target=f"target_{i}") for i in range(3)]

    resolved = hosted(server).resolve(requests)

    assert resolved == ["답", "답", "답"]
    assert server.calls == 3


def test_the_context_window_constant_matches_the_design() -> None:
    """#175's own design: up to four preceding utterances."""
    assert MAX_CONTEXT_UTTERANCES == 4
