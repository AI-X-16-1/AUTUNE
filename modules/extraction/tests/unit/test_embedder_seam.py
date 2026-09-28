"""The embedder seam (#175, #366): what a vector promises, and the two
implementations behind ``AUTUNE_EXTRACTION_EMBEDDER_IMPL``.

No ``hosted`` yet -- see ``pipeline.embedder``. This file is the seam itself:
the fake's contract and the registry's config-string wiring. The resolver's
own use of an embedder (the similarity check) is tested in
``test_resolver_seam.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from autune_extraction.config import ExtractionSettings
from autune_extraction.pipeline import FakeEmbedder, registry
from autune_extraction.pipeline.embedder import LocalKureEmbedder

# --- the fake: what runs before an embedder model exists --------------------


def test_the_fake_returns_one_vector_per_input_in_order() -> None:
    vectors = FakeEmbedder().embed(["첫 번째", "두 번째"])
    assert len(vectors) == 2


def test_the_fake_names_itself() -> None:
    assert FakeEmbedder().model_version == "fake"


def test_an_empty_batch_is_not_an_error() -> None:
    assert FakeEmbedder().embed([]) == []


def test_the_fake_is_deterministic_across_instances() -> None:
    """A test comparing two vectors needs the same text to always land on the
    same vector, in this run and the next -- see the module docstring's note
    about ``ord`` over ``hash`` for exactly this."""
    assert FakeEmbedder().embed(["같은 문장"]) == FakeEmbedder().embed(["같은 문장"])


def test_the_fakes_vectors_are_unit_length() -> None:
    """``Embedder.embed`` promises unit-normalised vectors -- callers take a
    dot product as a cosine similarity without renormalising."""
    (vector,) = FakeEmbedder().embed(["아무 문장이나"])
    norm = sum(v * v for v in vector) ** 0.5
    assert norm == pytest.approx(1.0)


def test_identical_text_is_maximally_similar_to_itself() -> None:
    (a,) = FakeEmbedder().embed(["동일한 문장"])
    (b,) = FakeEmbedder().embed(["동일한 문장"])
    assert sum(x * y for x, y in zip(a, b, strict=True)) == pytest.approx(1.0)


# --- the local embedder's own guards -----------------------------------------


def test_a_local_embedder_without_a_checkpoint_is_refused() -> None:
    with pytest.raises(ValueError, match="checkpoint"):
        LocalKureEmbedder("")


def test_the_local_embedder_records_its_checkpoint_as_its_version() -> None:
    embedder = LocalKureEmbedder("nlpai-lab/KURE-v1")
    assert embedder.model_version == "nlpai-lab/KURE-v1"


# --- the registry: config string -> implementation ---------------------------


def _settings(**overrides: Any) -> ExtractionSettings:
    """Declared defaults and nothing else. Same reason as
    ``test_classifier_seam.settings``."""
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    def apply(**overrides: Any) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: _settings(**overrides))

    registry.get_embedder.cache_clear()
    yield apply
    registry.get_embedder.cache_clear()


def test_the_default_embedder_is_the_fake(configured) -> None:
    configured()
    assert isinstance(registry.get_embedder(), FakeEmbedder)


def test_local_without_a_checkpoint_is_refused(configured) -> None:
    configured(embedder_impl="local", embedder_checkpoint="")
    with pytest.raises(ValueError, match="EMBEDDER_CHECKPOINT"):
        registry.get_embedder()


def test_local_with_the_default_checkpoint_builds(configured) -> None:
    configured(embedder_impl="local")
    embedder = registry.get_embedder()
    assert isinstance(embedder, LocalKureEmbedder)
    assert embedder.model_version == "nlpai-lab/KURE-v1"


def test_an_unknown_impl_is_refused(configured) -> None:
    configured(embedder_impl="external")
    with pytest.raises(ValueError, match="unknown"):
        registry.get_embedder()
