"""The NLI seam: step 4 (#12), whether a commitment or ambiguous utterance
actually entails a promise. No weights, no network -- see ``test_classifier_
seam.py``, which this mirrors.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator

import pytest
from pydantic import ValidationError

from autune_extraction.config import ExtractionSettings
from autune_extraction.pipeline import FakeNli, registry
from autune_extraction.pipeline.nli import HostedNli, LocalNli, _scores
from autune_extraction.pipeline.registry import _NLI

# --- what a score means ------------------------------------------------------


def test_the_label_is_the_argmax_of_the_three() -> None:
    scores = _scores(0.7, 0.2, 0.1)

    assert scores.label == "entailment"
    assert scores.entailment == pytest.approx(0.7)


def test_a_narrow_win_still_picks_a_label() -> None:
    assert _scores(0.34, 0.33, 0.33).label == "entailment"


# --- what has deliberately no implementation ---------------------------------


def test_there_is_no_external_nli() -> None:
    """Same reasoning as the classifier: privacy.md section 6 makes an
    external NLI endpoint a design conversation, not a config string.

    Asserted as the whole key set so adding one fails here, where the reason
    is written down, rather than passing as an ordinary feature.
    """
    assert set(_NLI) == {"local", "hosted", "fake"}


# --- the fake, which verify_utterances is built on ---------------------------


def test_the_fake_entails_a_real_commitment_marker() -> None:
    pair = ("내일까지 정리해서 공유드리겠습니다", "화자가 이 일을 하겠다고 약속했다")

    result = FakeNli().classify([pair])

    assert result[0].label == "entailment"


def test_the_fake_does_not_entail_weak_assent() -> None:
    """``한번 볼게요`` is #12's own example of assent too weak to promise anything."""
    result = FakeNli().classify([("한번 볼게요", "화자가 이 일을 하겠다고 약속했다")])

    assert result[0].label == "neutral"


def test_the_fake_contradicts_a_negated_commitment() -> None:
    result = FakeNli().classify([("이번엔 저는 못 합니다", "화자가 이 일을 하겠다고 약속했다")])

    assert result[0].label == "contradiction"


def test_the_fake_returns_one_result_per_pair_in_order() -> None:
    """Order is the contract: callers zip against their own utterance ids."""
    pairs = [
        ("제가 하겠습니다", "화자가 이 일을 하겠다고 약속했다"),
        ("한번 볼게요", "화자가 이 일을 하겠다고 약속했다"),
    ]

    result = FakeNli().classify(pairs)

    assert [r.label for r in result] == ["entailment", "neutral"]


def test_an_empty_batch_is_not_an_error() -> None:
    assert FakeNli().classify([]) == []


def test_the_fake_names_itself() -> None:
    assert FakeNli().model_version == "fake"


# --- the hosted NLI client ----------------------------------------------------


class Server:
    def __init__(self, results: list[dict[str, float]]) -> None:
        self.results = results
        self.calls: list[list[list[str]]] = []

    def request(self, method: str, path: str, *, json: dict) -> dict:
        self.calls.append(json["pairs"])
        return {"results": self.results}


def hosted(server: Server) -> HostedNli:
    client = HostedNli("http://inference.invalid", "ckpt")
    client._client = server
    return client


def test_the_hosted_client_reads_the_servers_scores() -> None:
    server = Server([{"entailment": 0.9, "contradiction": 0.05, "neutral": 0.05}])

    result = hosted(server).classify([("제가 하겠습니다", "화자가 이 일을 하겠다고 약속했다")])

    assert result[0].label == "entailment"
    assert result[0].entailment == pytest.approx(0.9)
    assert server.calls == [[["제가 하겠습니다", "화자가 이 일을 하겠다고 약속했다"]]]


def test_the_hosted_client_names_its_checkpoint() -> None:
    assert hosted(Server([])).model_version == "ckpt"


def test_an_empty_batch_sends_no_request() -> None:
    server = Server([])

    assert hosted(server).classify([]) == []
    assert server.calls == []


def test_a_server_answering_the_wrong_count_is_refused() -> None:
    """A server bug would otherwise zip the wrong result onto an utterance
    without an error."""
    server = Server([{"entailment": 1.0, "contradiction": 0.0, "neutral": 0.0}])

    with pytest.raises(ValueError, match="asked for 2 NLI results"):
        hosted(server).classify(
            [("a", "화자가 이 일을 하겠다고 약속했다"), ("b", "화자가 이 일을 하겠다고 약속했다")]
        )


# --- the device is chosen, not discovered -------------------------------------


def settings(**overrides: str) -> ExtractionSettings:
    """See ``test_classifier_seam.py``'s own ``settings`` for why ``_env_file=None``."""
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_the_default_nli_device_is_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_NLI_DEVICE", raising=False)

    assert settings().nli_device == "cpu"


@pytest.mark.parametrize("value", ["gpu", "CUDA", "cuda:0", "mps"])
def test_an_nli_device_name_we_do_not_handle_is_refused_at_startup(value: str) -> None:
    with pytest.raises(ValidationError):
        settings(nli_device=value)


@pytest.mark.parametrize("value", ["cpu", "cuda"])
def test_both_nli_devices_we_handle_are_accepted(value: str) -> None:
    assert settings(nli_device=value).nli_device == value


def test_asking_for_cuda_without_it_fails_before_the_model_loads() -> None:
    """See ``test_classifier_seam.py``'s own version of this test."""
    pytest.importorskip("transformers")
    torch = pytest.importorskip("torch")
    if torch.cuda.is_available():
        pytest.skip("this box has CUDA; the guard cannot fire")

    with pytest.raises(RuntimeError, match="no CUDA device"):
        LocalNli("klue/roberta-base", device="cuda")._load()


def test_a_missing_extra_is_reported_even_when_a_gpu_was_asked_for() -> None:
    """This is the case CI runs: it installs the workspace but not the extra."""
    if importlib.util.find_spec("transformers") is not None:
        pytest.skip("the extra is installed here; the missing-extra path cannot fire")

    with pytest.raises(RuntimeError, match="local-models"):
        LocalNli("klue/roberta-base", device="cuda")._load()


def test_local_nli_needs_a_checkpoint() -> None:
    with pytest.raises(ValueError, match="needs a checkpoint"):
        LocalNli("")


# --- there is no checkpoint to default to -------------------------------------


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Point the registry at chosen settings, and forget the cached NLI model
    on both sides so no test sees another's -- see ``test_classifier_seam.py``'s
    own ``configured``."""

    def configure(**overrides: str) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: settings(**overrides))

    registry.get_nli.cache_clear()
    yield configure
    registry.get_nli.cache_clear()


def test_the_default_nli_checkpoint_is_blank() -> None:
    """#172 settled on a checkpoint, but it is not baked in as a silent
    default -- see ``classifier_checkpoint``'s own note."""
    assert settings().nli_checkpoint == ""


@pytest.mark.parametrize("impl", ["local", "hosted"])
def test_a_real_nli_model_without_a_checkpoint_is_refused(
    configured: Callable[..., None], impl: str
) -> None:
    configured(nli_impl=impl, nli_endpoint="https://nli.internal")

    with pytest.raises(ValueError, match="AUTUNE_EXTRACTION_NLI_CHECKPOINT"):
        registry.get_nli()


def test_the_fake_needs_no_checkpoint(configured: Callable[..., None]) -> None:
    configured(nli_impl="fake")

    assert isinstance(registry.get_nli(), FakeNli)


def test_a_checkpoint_is_enough_to_build_the_local_nli_model(
    configured: Callable[..., None],
) -> None:
    """Construction loads nothing -- the weights come on the first NLI call --
    so this runs without the extra."""
    configured(nli_impl="local", nli_checkpoint="mminjae97/autune-context-kornli-klue-roberta")

    nli = registry.get_nli()

    assert isinstance(nli, LocalNli)
    assert nli.model_version == "mminjae97/autune-context-kornli-klue-roberta"


def test_hosted_nli_needs_an_endpoint(configured: Callable[..., None]) -> None:
    configured(nli_impl="hosted", nli_checkpoint="mminjae97/autune-context-kornli-klue-roberta")

    with pytest.raises(ValueError, match="AUTUNE_EXTRACTION_NLI_ENDPOINT"):
        registry.get_nli()


def test_an_unknown_nli_impl_is_refused(configured: Callable[..., None]) -> None:
    configured(nli_impl="cloud")

    with pytest.raises(ValueError, match="unknown AUTUNE_EXTRACTION_NLI_IMPL"):
        registry.get_nli()
