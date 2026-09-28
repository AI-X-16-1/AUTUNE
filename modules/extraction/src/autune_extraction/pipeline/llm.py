"""The utterance classifier as a cloud LLM call (``classifier_impl=llm``).

Why it exists: the 2026-09-23 mentoring reset the goal for the remaining weeks
to "everything works end to end, accuracy second", and said to use an LLM
wherever a trained model's accuracy is low. The fine-tuned DeBERTa's commitment
F1 on real Korean speech is ~0.3. This implementation, run against the real API
on the self-authored 8.txt dummy meeting (86 utterances, 16 commitment rows),
scored commitment F1 0.968 with gemini-3.8-flash and 0.909 with
gemini-3.5-flash-lite (2026-09-28).

**What leaves our infrastructure, and why it is allowed** (privacy.md section 6:
masked text only, and only what the feature needs):

- Utterance text only, already PII-masked at write time by module A. No
  speaker, no name, no timestamp, no meeting id, no utterance id -- the prompt
  numbers the lines 1..n within one request, and the answer is mapped back by
  position. The feature needs every utterance (it classifies every one), so the
  meeting goes out, but in requests of at most ``MAX_OUTBOUND_CHARS`` like
  ``HostedDeberta``'s, never as one body.
- Only utterances whose speaker consented: ``service.classify_utterances``
  filters before calling ``classify``, the same as for every implementation.
- Every request goes through ``autune_integrations.HttpClient``, so
  ``check_outbound`` scans every string in the body and refuses an unmasked
  phone number, e-mail or account number, and an oversized body.

Names are not masked by module A (there is no pattern for them), so a name said
aloud can be in the text. That is the same exposure the resolver (#366) and the
Notion sync already have, and it is why this is opt-in (``llm`` is never the
default) and why enabling it outside a demo is a team decision -- see the issue
this module's PR opened.

The LLM gives a label, not a probability. ``confidence`` is therefore a fixed
``LLM_CONFIDENCE``: the threshold ADR 0006 compares against is unset by default
(nothing is a candidate), and a made-up spread would be worse than a constant
that says "unscored".
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from autune_contracts.enums import UtteranceKind
from autune_core import get_logger
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .base import Prediction

log = get_logger(__name__)

LLM_CONFIDENCE = 0.9
"""What every answer reports as its confidence, a kind or none alike; the rest of
the probability mass goes to "none" for a kind and is spread over nothing for none."""

CONTEXT_LINES = 3
"""Earlier utterances sent before each request's own, marked as context only, so
an acceptance ("네 제가 할게요") can be read against the request before it."""

INSTRUCTIONS = (
    "회의 녹취록의 [대상] 줄마다 종류를 판단하세요.\n"
    "commitment: 화자가 할 일을 맡거나 요청을 수락함(기한 없어도 됨). "
    "decision: 회의가 무엇을 하기로 정함(보류·조건부 포함). "
    "open_question: 정보·의견·행동을 남에게 요청하거나 물음(요청 자체는 이것, 수락은 commitment). "
    "concern: 앞 말에 대한 반대·문제 제기. "
    "ambiguous: '검토해 볼게요'처럼 구체적 약속 없는 약한 동의, 다른 팀이 할 일 전달. "
    "그 외(설명·잡담·맞장구·투표·예상 수치)는 적지 마세요.\n"
    "[문맥] 줄은 판단하지 말고 참고만 하세요. "
    'JSON 한 줄로만 답하세요: {"labels": {"줄번호": "종류", ...}}. 해당 없으면 {"labels": {}}.'
)
"""Kept short on purpose: ``check_outbound`` counts these characters against the
same 4,000 as the utterances."""

_KINDS = {kind.value: kind for kind in UtteranceKind}
_RETRY_BACKOFF_SEC = (2.0, 5.0, 10.0)
"""Longer than ``HostedDeberta``'s: the provider answered 503 three times in a row
within seven seconds on 2026-09-28 -- a busy model, not a broken request."""
_BODY_OVERHEAD = len(INSTRUCTIONS) + 200
"""Instructions plus line markers and JSON punctuation the budget has to leave room for."""


def _llm_client(base_url: str, api_key: str, timeout_sec: float) -> Any:
    """The provider as a client the way every other outbound one is written.

    ``addressing`` names the keys whose values steer the request rather than
    carry meeting content, so ``check_outbound`` skips only those. The API key
    travels in a header, never in the body or the URL.

    **The read timeout is raised for this client only.** ``HttpClient``'s 10 s
    suits Slack and Notion; a thinking model answering one window took 12-20 s
    against the real API (2026-09-28), so every window timed out, was retried,
    and a timed-out request may still be billed. The shared default stays as it
    is -- this client sets its own.
    """
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class LlmClient(HttpClient):
        service = "extraction-llm-classifier"
        addressing = frozenset({"role", "responseMimeType"})

    client = LlmClient(base_url, headers={"x-goog-api-key": api_key})
    client._client.timeout = timeout_sec  # noqa: SLF001 - httpx's own setter; see above
    return client


def windows(texts: list[str], budget: int) -> list[tuple[int, int]]:
    """``[start, end)`` ranges of ``texts`` whose own text plus up to
    ``CONTEXT_LINES`` of context fits ``budget`` characters.

    A single utterance longer than the budget still gets a window of its own;
    ``check_outbound`` then refuses it by name rather than this silently
    truncating what the classifier sees.
    """
    out: list[tuple[int, int]] = []
    start = 0
    while start < len(texts):
        context = sum(len(t) + 12 for t in texts[max(0, start - CONTEXT_LINES) : start])
        size, end = context, start
        while end < len(texts) and (end == start or size + len(texts[end]) + 12 <= budget):
            size += len(texts[end]) + 12
            end += 1
        out.append((start, end))
        start = end
    return out


def render(texts: list[str], start: int, end: int) -> tuple[str, dict[int, int]]:
    """The request's lines, numbered 1..n, and line number -> index into ``texts``."""
    lines: list[str] = []
    targets: dict[int, int] = {}
    for n, i in enumerate(range(max(0, start - CONTEXT_LINES), end), start=1):
        tag = "대상" if i >= start else "문맥"
        lines.append(f"{n} [{tag}] {texts[i]}")
        if i >= start:
            targets[n] = i
    return "\n".join(lines), targets


def parse(answer: str) -> dict[int, UtteranceKind]:
    """``{"labels": {"3": "commitment"}}`` -> ``{3: COMMITMENT}``. Anything else
    in the answer -- prose around the JSON, an unknown kind, a non-number key --
    is dropped rather than guessed at."""
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        return {}
    try:
        labels = json.loads(match.group(0)).get("labels", {})
    except (json.JSONDecodeError, AttributeError):
        return {}
    if not isinstance(labels, dict):
        return {}
    out: dict[int, UtteranceKind] = {}
    for key, value in labels.items():
        kind = _KINDS.get(str(value).strip())
        if kind is not None and str(key).strip().isdigit():
            out[int(str(key).strip())] = kind
    return out


def _prediction(kind: UtteranceKind | None) -> Prediction:
    if kind is None:
        return Prediction(
            kind=None,
            confidence=LLM_CONFIDENCE,
            scores=dict.fromkeys(UtteranceKind, 0.0),
            none_score=LLM_CONFIDENCE,
        )
    scores = dict.fromkeys(UtteranceKind, 0.0)
    scores[kind] = LLM_CONFIDENCE
    return Prediction(
        kind=kind, confidence=LLM_CONFIDENCE, scores=scores, none_score=1.0 - LLM_CONFIDENCE
    )


class LlmClassifier:
    """Gemini's ``generateContent`` over masked utterances, one window at a time."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_sec: float = 60.0,
        fallback_model: str = "",
    ) -> None:
        self._client = _llm_client(base_url, api_key, timeout_sec)
        self._model = model
        self._fallback = fallback_model

    @property
    def model_version(self) -> str:
        """``llm:<model>``, or ``llm:<model>+<fallback>`` when a fallback is set.

        With a fallback, any window may have been answered by either model, so
        the pair is what produced the meeting's rows -- recording only the first
        would attribute the second's answers to it."""
        return f"llm:{self._model}" + (f"+{self._fallback}" if self._fallback else "")

    def _post_to(self, model: str, body: dict[str, Any], *, index: int) -> Any:
        """Same retry shape as ``HostedDeberta._post``: transient failures only."""
        path = f"/models/{model}:generateContent"
        for attempt, wait in enumerate(_RETRY_BACKOFF_SEC, start=1):
            try:
                return self._client.request("POST", path, json=body)
            except TransientIntegrationError as exc:
                # Counts and a reason only; the body is utterances.
                log.info(
                    "extraction_llm_retry",
                    model=model,
                    window=index,
                    attempt=attempt,
                    reason=str(exc),
                )
                time.sleep(wait)
        return self._client.request("POST", path, json=body)

    def _post(self, body: dict[str, Any], *, index: int) -> Any:
        """The primary model, then -- if it stays unavailable -- the fallback.

        Only a *transient* failure falls back (a 429, a 5xx, a timeout): the
        request was fine and the model was busy. A 4xx or a privacy refusal is
        about the request itself and would fail the same way on any model."""
        try:
            return self._post_to(self._model, body, index=index)
        except TransientIntegrationError:
            if not self._fallback:
                raise
            log.warning(
                "extraction_llm_fallback", model=self._model, fallback=self._fallback, window=index
            )
            return self._post_to(self._fallback, body, index=index)

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        kinds: list[UtteranceKind | None] = [None] * len(texts)
        for index, (start, end) in enumerate(windows(texts, MAX_OUTBOUND_CHARS - _BODY_OVERHEAD)):
            text, targets = render(texts, start, end)
            body = {
                "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            }
            answer = _answer_text(self._post(body, index=index))
            for line, kind in parse(answer).items():
                if line in targets:
                    kinds[targets[line]] = kind
        log.info(
            "extraction_llm_classified",
            utterances=len(texts),
            labelled=sum(k is not None for k in kinds),
        )
        return [_prediction(kind) for kind in kinds]


def _answer_text(body: Any) -> str:
    """The first candidate's text, or "" -- a blocked or empty answer labels nothing."""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        return ""
