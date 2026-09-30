"""The verifier provider layer — ``pipeline.verifier``.

Nothing here calls a real LLM. The Gemini client is driven through an httpx
mock transport, which is also how the tests see exactly what a request would
have carried.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_core.errors import PrivacyViolationError
from autune_gap import template, verification
from autune_gap.config import get_settings
from autune_gap.pipeline import FakeVerifier, GeminiVerifier, TemplateVerifier
from autune_gap.pipeline.registry import _VERIFIERS, get_template_verifier, reset_cache
from autune_gap.pipeline.verifier import INSTRUCTIONS, batches, parse, render
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, check_outbound

GENERAL = template.get_template("general")


def question(text: str, *keys: str) -> verification.Question:
    decisions = [verification.Decision(0, verification.Triage.AMBIGUOUS, candidates=keys)]
    return verification.questions(decisions, [text], GENERAL, examples_per_candidate=2, limit=1)[0][
        1
    ]


REINDEX = question(
    "인덱스 재색인이 먼저 끝나야 정렬 로직을 붙일 수 있습니다", "next_step", "dependency"
)
CLOSING = question("네 알겠습니다. 그럼 여기서 마치겠습니다", "ownership", "dependency")


# --- the prompt --------------------------------------------------------------------


def test_the_prompt_letters_each_candidate_once() -> None:
    text, offered = render([REINDEX, CLOSING])

    assert text.count("의존성·선행 조건") == 1
    assert offered[1] == {"A": "next_step", "B": "dependency"}
    assert offered[2] == {"C": "ownership", "B": "dependency"}


def test_the_prompt_carries_the_utterances_asked_about_and_nothing_else() -> None:
    text, _ = render([REINDEX])

    assert "[발화 1] 인덱스 재색인이 먼저 끝나야 정렬 로직을 붙일 수 있습니다" in text
    assert "[발화 2]" not in text


def test_the_prompt_offers_only_the_candidates_not_the_whole_template() -> None:
    text, _ = render([REINDEX])

    assert "담당자와 기한" not in text
    assert "성공 기준" not in text


def test_a_realistic_request_passes_the_outbound_check() -> None:
    """Every string in the body is scanned before it leaves. The template's own
    examples carry numbers ("20%", "30일") and must not trip it."""
    text, _ = render([REINDEX, CLOSING])
    body = {
        "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
    }

    check_outbound(body, destination="test", addressing=frozenset({"role"}))


# --- the answer --------------------------------------------------------------------


def test_an_answer_maps_letters_back_to_item_keys() -> None:
    _, offered = render([REINDEX, CLOSING])

    parsed = parse('{"answers": {"1": ["B"], "2": []}}', offered)

    assert parsed == {1: {"dependency"}, 2: frozenset()}


def test_a_letter_the_line_was_not_offered_is_dropped() -> None:
    """Line 2 was offered C and B. "A" is next_step, offered only to line 1, and
    "Z" is nothing at all; neither may reach line 2."""
    _, offered = render([REINDEX, CLOSING])

    parsed = parse('{"answers": {"2": ["A", "Z", "C"]}}', offered)

    assert parsed[2] == {"ownership"}


def test_a_line_the_answer_skips_is_answered_empty() -> None:
    _, offered = render([REINDEX, CLOSING])

    assert parse('{"answers": {"1": ["A"]}}', offered)[2] == frozenset()


def test_an_answer_with_no_json_is_refused() -> None:
    _, offered = render([REINDEX])

    with pytest.raises(ValueError):
        parse("의존성입니다", offered)


# --- batching ---------------------------------------------------------------------


def test_questions_are_batched_under_the_budget() -> None:
    many = [REINDEX] * 60
    groups = batches(many, 2000)

    assert sum(len(group) for group in groups) == 60
    assert all(len(render(group)[0]) <= 2000 for group in groups)


def test_one_oversized_question_still_gets_a_batch_of_its_own() -> None:
    long = question("가" * 3000, "dependency")

    assert batches([long], 1000) == [[long]]


# --- the Gemini client --------------------------------------------------------------


def gemini(handler: Any) -> GeminiVerifier:
    verifier = GeminiVerifier(
        api_key="test-key",
        model="gemini-test",
        base_url="https://example.invalid/v1beta",
        timeout_sec=5,
    )
    verifier._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://example.invalid/v1beta", transport=httpx.MockTransport(handler)
    )
    return verifier


def reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_the_request_carries_the_question_and_the_key_in_a_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply('{"answers": {"1": ["B"]}}')

    verifier = gemini(handler)
    verifier._client._client.headers["x-goog-api-key"] = "test-key"  # noqa: SLF001

    answers = verifier.verify([REINDEX])

    assert answers == [{"dependency"}]
    request = seen[0]
    assert request.url.path.endswith("/models/gemini-test:generateContent")
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in request.content.decode()
    body = json.loads(request.content)
    sent = body["contents"][0]["parts"][0]["text"]
    assert "인덱스 재색인이 먼저 끝나야" in sent
    assert len(json.dumps(body, ensure_ascii=False)) <= MAX_OUTBOUND_CHARS


def test_a_failed_request_leaves_its_questions_unanswered() -> None:
    """Never raises: the caller keeps the embedding's answer for these."""
    verifier = gemini(lambda request: httpx.Response(400, json={}))

    assert verifier.verify([REINDEX, CLOSING]) == [None, None]


def test_an_unparseable_answer_leaves_its_questions_unanswered() -> None:
    verifier = gemini(lambda request: reply("모르겠습니다"))

    assert verifier.verify([REINDEX]) == [None]


def test_an_unmasked_phone_number_is_refused_and_raised() -> None:
    """``check_outbound`` runs on the body and refuses it, so nothing leaves.
    The refusal is **raised, not answered**: module A masks before it writes,
    so an unmasked number here means a stored transcript broke invariant 11,
    and falling back to the embedding would hide that (review of #484)."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply('{"answers": {}}')

    leaked = question("연락은 010-1234-5678로 주세요", "ownership")

    with pytest.raises(PrivacyViolationError):
        gemini(handler).verify([leaked])
    assert sent == []


def test_an_utterance_too_long_to_send_is_not_sent() -> None:
    """Not sending is the rule for what does not fit, and it is not a privacy
    failure: the question goes unanswered and the embedding answers for it."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply('{"answers": {"1": ["B"]}}')  # B is dependency in REINDEX's batch

    long = question("가" * (MAX_OUTBOUND_CHARS + 10), "dependency")

    assert gemini(handler).verify([long, REINDEX]) == [None, {"dependency"}]
    assert len(sent) == 1
    assert "가" * 50 not in sent[0].content.decode()


def test_gemini_refuses_to_start_without_a_key() -> None:
    with pytest.raises(ValueError, match="AUTUNE_GAP_VERIFIER_API_KEY"):
        GeminiVerifier(api_key="", model="m", base_url="https://example.invalid", timeout_sec=1)


def test_an_addressing_key_holding_an_object_is_refused() -> None:
    from autune_gap.pipeline.gemini import require_scalar_addressing  # noqa: PLC0415

    with pytest.raises(PrivacyViolationError):
        require_scalar_addressing({"role": {"text": "x"}}, frozenset({"role"}))


# --- the registry -------------------------------------------------------------------


def test_the_verifier_set_names_its_one_external_entry() -> None:
    """``gemini`` sends utterances to Google. Asserted whole, so another external
    provider is a decision somebody made rather than a key that appeared."""
    assert set(_VERIFIERS) == {"off", "fake", "gemini"}
    assert "EXTERNAL" in _VERIFIERS["gemini"]


def test_the_fakes_satisfy_the_protocol() -> None:
    assert isinstance(FakeVerifier(), TemplateVerifier)


@pytest.fixture
def settings_restored() -> Any:
    settings = get_settings()
    saved = (settings.verifier_impl, settings.embedder_impl)
    reset_cache()
    yield settings
    settings.verifier_impl, settings.embedder_impl = saved
    reset_cache()


def test_the_verifier_is_off_by_default(settings_restored: Any) -> None:
    settings_restored.verifier_impl = "off"

    assert get_template_verifier() is None


def test_a_verifier_without_the_embedder_is_refused(settings_restored: Any) -> None:
    settings_restored.verifier_impl = "fake"
    settings_restored.embedder_impl = "off"

    with pytest.raises(ValueError, match="AUTUNE_GAP_EMBEDDER_IMPL"):
        get_template_verifier()


def test_an_unknown_verifier_is_named_in_the_error(settings_restored: Any) -> None:
    settings_restored.verifier_impl = "openai"

    with pytest.raises(ValueError, match="AUTUNE_GAP_VERIFIER_IMPL"):
        get_template_verifier()
