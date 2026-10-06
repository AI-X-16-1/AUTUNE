"""``classifier_impl=llm``: what it sends, what it reads back, and when it refuses.

No network. A fake server stands in for the provider except in the one test
that needs the real ``HttpClient`` guard, which refuses before anything is sent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from autune_contracts.enums import UtteranceKind
from autune_core.errors import PrivacyViolationError
from autune_extraction.config import ExtractionSettings
from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline import registry
from autune_extraction.pipeline.llm import (
    CONTEXT_LINES,
    INSTRUCTIONS,
    LLM_CONFIDENCE,
    LlmClassifier,
    parse,
    pieces,
    strongest,
    windows,
)
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.privacy import check_outbound

API_KEY = "test-key-never-in-a-body"
_dumps = json.dumps  # Provider.request takes httpx's `json=` keyword, which shadows the module


class Provider:
    """Answers like ``generateContent``: labels every ``[대상]`` line that ends a
    promise ("할게요") as a commitment, and -- to prove they are ignored -- every
    ``[문맥]`` line as a decision."""

    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.bodies: list[dict] = []
        self.paths: list[str] = []

    def request(self, method: str, path: str, *, json: dict) -> dict:  # noqa: A002 - httpx's name
        self.paths.append(path)
        self.bodies.append(json)
        if self.failures:
            raise self.failures.pop(0)
        text = json["contents"][0]["parts"][0]["text"]
        labels = {}
        for line in text.splitlines():
            m = re.match(r"(\d+) \[(대상|문맥)\] (.*)", line)
            if m and m[2] == "문맥":
                labels[m[1]] = "decision"
            elif m and m[3].endswith("할게요"):
                labels[m[1]] = "commitment"
        answer = "여기 결과입니다: " + _dumps({"labels": labels}, ensure_ascii=False)
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(llm_module.time, "sleep", waits.append)
    return waits


def classifier(provider: Provider) -> LlmClassifier:
    c = LlmClassifier(api_key=API_KEY, model="gemini-test", base_url="http://llm.invalid")
    c._client = provider  # type: ignore[assignment]
    return c


def meeting(n: int) -> list[str]:
    """``n`` distinct masked-looking utterances; every fifth is a promise."""
    return [
        f"{i}번째로 그 일정 얘기를 좀 해 보면 제가 금요일까지 정리할게요"
        if i % 5 == 0
        else f"{i}번째 안건은 이제 뭐 일단 좀 더 이야기해 보고 넘어가죠"
        for i in range(n)
    ]


# --- what it reads back ---------------------------------------------------------


def test_labels_map_back_by_position_and_context_lines_are_never_labelled(slept) -> None:
    texts = meeting(60)
    predictions = classifier(Provider()).classify(texts)

    assert len(predictions) == len(texts)
    got = [p.kind for p in predictions]
    want = [UtteranceKind.COMMITMENT if i % 5 == 0 else None for i in range(60)]
    # The provider labels every context line "decision"; none of that may land.
    assert got == want
    assert all(p.confidence == LLM_CONFIDENCE for p in predictions)


def test_parse_drops_what_it_cannot_read() -> None:
    assert parse('앞말 {"labels": {"2": "commitment", "x": "decision", "3": "vote"}} 뒷말') == {
        2: UtteranceKind.COMMITMENT
    }
    assert parse("no json at all") == {}
    assert parse('{"labels": ["commitment"]}') == {}


def test_a_blocked_or_empty_answer_labels_nothing(slept) -> None:
    provider = Provider()
    provider.request = lambda *a, **k: {"candidates": []}  # type: ignore[method-assign]
    predictions = classifier(provider).classify(meeting(10))
    assert [p.kind for p in predictions] == [None] * 10


def test_no_texts_no_request(slept) -> None:
    provider = Provider()
    assert classifier(provider).classify([]) == []
    assert provider.bodies == []


# --- what it sends --------------------------------------------------------------


def test_a_long_meeting_goes_out_in_requests_the_outbound_guard_accepts(slept) -> None:
    """A 30-minute meeting is ~270 utterances; one body would be ~13,000 chars."""
    provider = Provider()
    classifier(provider).classify(meeting(300))

    assert len(provider.bodies) > 1
    for body in provider.bodies:
        check_outbound(body, destination="test", addressing=frozenset({"role", "responseMimeType"}))


def test_each_window_carries_the_lines_before_it_as_context(slept) -> None:
    provider = Provider()
    classifier(provider).classify(meeting(300))
    second = provider.bodies[1]["contents"][0]["parts"][0]["text"].splitlines()
    assert [line.split(" ")[1] for line in second[:CONTEXT_LINES]] == ["[문맥]"] * CONTEXT_LINES
    assert second[CONTEXT_LINES].split(" ")[1] == "[대상]"


def turns(n: int, chars: int) -> list[str]:
    """``n`` distinct turns of about ``chars`` characters each -- one person talking
    for a while. Every fifth still ends in a promise."""
    filler = "그 부분은 지난번에 이야기한 흐름대로 조금 더 살펴보면 좋겠고요 "
    return [
        f"{i}번째로 " + (filler * (chars // len(filler) + 1))[:chars] + tail
        for i, tail in enumerate(meeting(n))
    ]


def _lines(body: dict) -> list[tuple[str, str]]:
    """``(tag, text)`` of each line of one request."""
    text = body["contents"][0]["parts"][0]["text"]
    return [(m[1], m[2]) for m in re.finditer(r"^\d+ \[(대상|문맥)\] (.*)$", text, re.M)]


def test_a_meeting_of_long_turns_still_goes_out_in_requests_the_guard_accepts(slept) -> None:
    """dev, 2026-10-05: 82 utterances, and the second request was over the limit
    -- three long context lines and then a target that had to be admitted. The
    meeting stored no action item and no decision."""
    provider = Provider()
    texts = turns(82, 900)
    predictions = classifier(provider).classify(texts)

    for body in provider.bodies:
        check_outbound(body, destination="test", addressing=frozenset({"role", "responseMimeType"}))
    targets = [text for body in provider.bodies for tag, text in _lines(body) if tag == "대상"]
    assert targets == texts  # each one asked about once, in order
    assert [p.kind for p in predictions] == [
        UtteranceKind.COMMITMENT if i % 5 == 0 else None for i in range(82)
    ]


def test_context_gives_way_before_a_target_does_and_the_nearest_line_stays(slept) -> None:
    provider = Provider()
    texts = turns(12, 900)
    classifier(provider).classify(texts)

    for body in provider.bodies[1:]:
        lines = _lines(body)
        context = [text for tag, text in lines if tag == "문맥"]
        first = next(text for tag, text in lines if tag == "대상")
        at = texts.index(first)
        assert 0 < len(context) < CONTEXT_LINES
        assert context == texts[at - len(context) : at]


def test_a_turn_longer_than_a_request_is_asked_about_in_pieces(slept) -> None:
    """Its own request would be refused and, before, took the meeting with it.
    Left out, the promise it ends in would be lost; so it goes in pieces."""
    provider = Provider()
    texts = turns(20, 300)
    texts[7] = turns(1, 3200)[0]  # ends in a promise, as turn 0 does
    with capture_logs() as logs:
        predictions = classifier(provider).classify(texts)

    for body in provider.bodies:
        check_outbound(body, destination="test", addressing=frozenset({"role", "responseMimeType"}))
    targets = [text for body in provider.bodies for tag, text in _lines(body) if tag == "대상"]
    parts = targets[7:-12]
    assert targets[:7] == texts[:7] and targets[-12:] == texts[8:]
    assert len(parts) > 1 and " ".join(parts) == texts[7]  # all of it, in order, once
    assert predictions[7].kind is UtteranceKind.COMMITMENT  # the last piece's
    assert [p.kind for p in predictions].count(UtteranceKind.COMMITMENT) == 5  # 0, 5, 7, 10, 15
    (entry,) = [e for e in logs if e["event"] == "extraction_llm_classified"]
    assert entry["in_pieces"] == 1 and entry["utterances"] == 20
    assert texts[7][:40] not in repr(logs)


def test_a_turn_one_request_can_carry_is_sent_whole(slept) -> None:
    provider = Provider()
    texts = turns(3, 300)
    texts[1] = turns(1, 2500)[0]
    classifier(provider).classify(texts)

    targets = [text for body in provider.bodies for tag, text in _lines(body) if tag == "대상"]
    assert targets == texts


def test_pieces_end_after_a_sentence_and_lose_nothing() -> None:
    text = "첫 문장은 여기서 끝납니다. 둘째 문장은 조금 더 길게 이어집니다. 셋째는 짧아요."
    got = pieces(text, 40)

    assert all(len(piece) <= 40 for piece in got)
    assert " ".join(got) == text
    assert got[0].endswith("끝납니다.") or got[0].endswith("이어집니다.")
    # No sentence end and no space in reach: cut at the limit, still nothing lost.
    assert pieces("가" * 95, 40) == ["가" * 40, "가" * 40, "가" * 15]
    assert pieces("짧은 말", 40) == ["짧은 말"] and pieces("", 40) == [""]


def test_of_several_pieces_the_kind_the_record_is_made_from_wins() -> None:
    kind = UtteranceKind
    assert strongest([kind.CONCERN, None, kind.DECISION]) is kind.DECISION
    assert strongest([kind.AMBIGUOUS, kind.COMMITMENT, kind.DECISION]) is kind.COMMITMENT
    assert strongest([kind.OPEN_QUESTION, kind.CONCERN]) is kind.OPEN_QUESTION
    assert strongest([None, None]) is None and strongest([]) is None


def test_no_window_holds_a_line_one_request_cannot_carry() -> None:
    """``classify`` never passes one; if something else does, the line is left
    out rather than sent to be refused with everything after it."""
    assert windows(["가" * 500, "나" * 10, "다" * 10], 100) == [(1, 1, 3)]


def test_the_body_holds_utterance_text_and_the_instructions_and_nothing_else(slept) -> None:
    """No speaker, no id, no time, no meeting -- and never the API key."""
    provider = Provider()
    texts = meeting(8)
    classifier(provider).classify(texts)
    body = provider.bodies[0]

    assert body["systemInstruction"]["parts"][0]["text"] == INSTRUCTIONS
    lines = body["contents"][0]["parts"][0]["text"].splitlines()
    assert [re.sub(r"^\d+ \[대상\] ", "", line) for line in lines] == texts
    assert API_KEY not in json.dumps(body, ensure_ascii=False)
    assert provider.paths == ["/models/gemini-test:generateContent"]


def test_an_addressing_key_holding_an_object_is_refused_before_anything_is_sent() -> None:
    """mkkim68, review of #405: ``check_outbound`` skips everything under an
    addressing key. If ``role`` ever held an object, the phone number inside
    would leave unchecked -- so the client refuses the body first."""
    client = llm_module._llm_client("http://llm.invalid", API_KEY, 5.0)
    body = {"contents": [{"role": {"note": "010-1234-5678"}, "parts": [{"text": "안녕하세요"}]}]}

    with pytest.raises(PrivacyViolationError, match="'role'"):
        client.request("POST", "/models/m:generateContent", json=body)


@pytest.mark.parametrize("key", ["role", "responseMimeType"])
def test_string_addressing_values_pass(key: str) -> None:
    llm_module.require_scalar_addressing({"a": [{key: "user"}]}, frozenset({key}))


def test_an_unmasked_phone_number_is_refused_before_anything_is_sent() -> None:
    """The real client, so the real guard: it raises before the (invalid) host
    is ever contacted. A masking miss upstream stops here, loudly."""
    real = LlmClassifier(api_key=API_KEY, model="m", base_url="http://llm.invalid")
    with pytest.raises(PrivacyViolationError):
        real.classify(["제 번호 010-1234-5678로 연락 주세요"])


def test_a_transient_failure_is_retried(slept) -> None:
    provider = Provider(TransientIntegrationError("503"))
    predictions = classifier(provider).classify(meeting(5))
    assert predictions[0].kind == UtteranceKind.COMMITMENT
    assert len(slept) == 1


def test_a_model_that_stays_busy_hands_the_window_to_the_fallback(slept) -> None:
    """Four 503s exhaust the primary's attempts; the fallback answers."""
    provider = Provider(*[TransientIntegrationError("503")] * 4)
    c = classifier(provider)
    c._fallback = "gemini-lite"
    predictions = c.classify(meeting(5))
    assert predictions[0].kind == UtteranceKind.COMMITMENT
    assert provider.paths[-1] == "/models/gemini-lite:generateContent"
    assert c.model_version == "llm:gemini-test+gemini-lite"


def test_without_a_fallback_a_busy_model_fails_the_call(slept) -> None:
    provider = Provider(*[TransientIntegrationError("503")] * 4)
    with pytest.raises(TransientIntegrationError):
        classifier(provider).classify(meeting(5))


# --- how it is chosen -------------------------------------------------------------


SHARED_KEY = "AUTUNE_LLM_API_KEY"
"""The shared key's name in the environment, which is also how a test passes it:
the field takes its alias, not its own name."""


def key_of(configured_settings: ExtractionSettings) -> str:
    return configured_settings.llm_api_key.get_secret_value()


def settings(**overrides: str) -> ExtractionSettings:
    # Both key names blank unless a test gives one: a key exported in the shell
    # that runs the suite must not decide what "no key" means.
    # These tests are about what an LLM implementation does once it is on;
    # that it must be acknowledged first has its own file
    # (test_llm_acknowledged_392.py).
    given = {"llm_api_key": "", SHARED_KEY: "", "llm_acknowledged_392": True} | overrides
    return ExtractionSettings(_env_file=None, **given)  # type: ignore[arg-type]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    def configure(**overrides: str) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: settings(**overrides))

    registry.get_classifier.cache_clear()
    yield configure
    registry.get_classifier.cache_clear()


def test_llm_is_never_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CLASSIFIER_IMPL", raising=False)
    assert settings().classifier_impl != "llm"


def test_llm_without_a_key_is_refused_by_name(configured) -> None:
    configured(classifier_impl="llm")
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        registry.get_classifier()


def test_llm_without_its_own_key_uses_the_deployments_shared_one(configured) -> None:
    """``AUTUNE_LLM_API_KEY`` is the name the deployment's secret has. B's own
    name ships blank in ``.env.example``, and blank has to fall through."""
    configured(classifier_impl="llm", **{SHARED_KEY: "shared"})

    assert isinstance(registry.get_classifier(), LlmClassifier)
    assert settings(**{SHARED_KEY: "shared"}).llm_api_key.get_secret_value() == "shared"


def test_a_key_given_to_b_alone_wins_over_the_shared_one() -> None:
    both = settings(llm_api_key="mine", **{SHARED_KEY: "shared"})

    assert both.llm_api_key.get_secret_value() == "mine"


def test_the_shared_key_alone_does_not_turn_the_llm_on() -> None:
    """A deployment sets it for another module; B still classifies locally
    until ``classifier_impl`` or ``resolver_impl`` says otherwise (#392)."""
    only_a_key = settings(**{SHARED_KEY: "shared"})

    assert not only_a_key.classifier_impl.startswith("llm")
    assert only_a_key.resolver_impl != "llm"


def test_both_names_are_read_from_the_environment_and_a_blank_own_name_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """From real variables and from a ``.env``, the two places a key comes from."""
    monkeypatch.setenv("AUTUNE_EXTRACTION_LLM_API_KEY", "")
    monkeypatch.setenv("AUTUNE_LLM_API_KEY", "from-the-environment")
    assert key_of(ExtractionSettings(_env_file=None)) == "from-the-environment"  # type: ignore[call-arg]

    monkeypatch.delenv("AUTUNE_EXTRACTION_LLM_API_KEY")
    monkeypatch.delenv("AUTUNE_LLM_API_KEY")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AUTUNE_EXTRACTION_LLM_API_KEY=\nAUTUNE_LLM_API_KEY=from-the-file\n", encoding="utf-8"
    )
    assert key_of(ExtractionSettings(_env_file=env_file)) == "from-the-file"  # type: ignore[call-arg]

    monkeypatch.setenv("AUTUNE_EXTRACTION_LLM_API_KEY", "mine")
    assert key_of(ExtractionSettings(_env_file=env_file)) == "mine"  # type: ignore[call-arg]


def test_printing_or_dumping_the_settings_does_not_show_either_key() -> None:
    """mkkim68 and mminjae97, review of #701: as plain strings both keys were
    in ``repr(settings)`` and in ``model_dump()``, which is what a debug log
    line or an error report prints."""
    configured_ = settings(llm_api_key="own-key-value", **{SHARED_KEY: "shared-key-value"})

    shown = repr(configured_) + str(configured_) + str(configured_.model_dump())
    shown += configured_.model_dump_json()

    assert "own-key-value" not in shown
    assert "shared-key-value" not in shown


def test_the_client_is_given_the_key_itself_not_its_mask(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of hiding it: what goes in the request header has to
    be the key, and ``str(SecretStr)`` is the asterisks."""
    given: dict[str, object] = {}

    class Recording:
        def __init__(self, **kwargs: object) -> None:
            given.update(kwargs)

    monkeypatch.setattr(llm_module, "LlmClassifier", Recording)
    configured(classifier_impl="llm", **{SHARED_KEY: "shared-key-value"})

    registry.get_classifier()

    assert given["api_key"] == "shared-key-value"


def test_llm_with_a_key_is_the_llm_classifier(configured) -> None:
    configured(classifier_impl="llm", llm_api_key="k", llm_model="gemini-3.8-flash")
    chosen = registry.get_classifier()
    assert isinstance(chosen, LlmClassifier)
    assert chosen.model_version == "llm:gemini-3.8-flash+gemini-3.5-flash-lite"


def test_the_llm_client_waits_longer_than_the_shared_default() -> None:
    """A thinking model's window took 12-20 s against the real API; the shared
    client's 10 s turned every window into a timeout and a retry."""
    real = LlmClassifier(api_key=API_KEY, model="m", base_url="http://llm.invalid", timeout_sec=45)
    assert real._client._client.timeout.read == 45
