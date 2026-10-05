"""``resolver_impl=llm`` with several items in one call: what is sent, what comes back.

No network: a fake provider stands in for Gemini and records every body and the
model each was sent to. The rules under test: id-carrying requests share a call
up to ``MAX_BATCH`` and the outbound limit, each answer is checked exactly as a
single one is, and what a batch leaves unresolved costs one more call at most.
"""

from __future__ import annotations

import json
import re
from typing import Any

from autune_extraction.pipeline.base import ResolutionRequest
from autune_extraction.pipeline.resolver import (
    _DECISION_TASK,
    _PROMPT_BUDGET,
    _SUMMARY_TASK,
    MAX_BATCH,
    MAX_ESCALATIONS,
    LlmResolver,
)


class Provider:
    """Answers like ``generateContent``, in order, and records where each went."""

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.models: list[str] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.bodies.append(json)
        self.models.append(path.split("/models/")[1].split(":")[0])
        answer = self.answers.pop(0) if self.answers else ""
        if isinstance(answer, Exception):
            raise answer
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}

    def prompt(self, n: int) -> str:
        return str(self.bodies[n]["contents"][0]["parts"][0]["text"])


def resolver(provider: Provider, *, second: str = "") -> LlmResolver:
    r = LlmResolver(
        api_key="k", model="first", base_url="http://llm.invalid", fallback_model=second
    )
    r._client = provider  # type: ignore[assignment]
    return r


def item(n: int, *, purpose: str = "commitment", target: str = "") -> ResolutionRequest:
    return ResolutionRequest(
        target=target or f"그 {n}번 버그는 제가 이번 빌드에 넣어 볼게요",
        context=("네 그렇죠",),
        target_id=f"t{n}",
        context_ids=(f"c{n}",),
        related=((f"r{n}", f"클라 갱신이 늦는 {n}번 버그는 서버에서 고쳤어요"),),
        purpose=purpose,
    )


def summary(n: int) -> str:
    return f"클라 갱신이 늦는 그 {n}번 버그는 제가 이번 빌드에 넣어 볼게요"


def items(*answers: tuple[int, str, list[Any]]) -> str:
    return json.dumps(
        {"items": [{"item": k, "summary": s, "used": u} for k, s, u in answers]},
        ensure_ascii=False,
    )


def test_three_items_are_one_call_and_each_keeps_its_own_summary_and_lines() -> None:
    provider = Provider(items((1, summary(1), [3]), (2, summary(2), [3]), (3, summary(3), [3])))

    out = resolver(provider).resolve_with_evidence([item(1), item(2), item(3)])

    assert len(provider.bodies) == 1
    assert [r.text for r in out] == [summary(1), summary(2), summary(3)]
    assert [r.used for r in out] == [("r1",), ("r2",), ("r3",)]


def test_each_item_is_numbered_from_one_under_its_own_heading() -> None:
    provider = Provider(items((1, summary(1), []), (2, summary(2), [])))

    resolver(provider).resolve_with_evidence([item(1), item(2)])

    prompt = provider.prompt(0)
    assert "항목 1\n1 [앞] 네 그렇죠\n2 [대상] 그 1번 버그" in prompt
    assert "항목 2\n1 [앞] 네 그렇죠\n2 [대상] 그 2번 버그" in prompt
    assert provider.bodies[0]["generationConfig"] == {
        "temperature": 0,
        "responseMimeType": "application/json",
    }


def test_the_batch_carries_the_measured_rules_and_examples_word_for_word() -> None:
    commitments, decisions = Provider(), Provider()

    resolver(commitments).resolve_with_evidence([item(1), item(2)])
    resolver(decisions).resolve_with_evidence(
        [item(1, purpose="decision"), item(2, purpose="decision")]
    )

    assert _SUMMARY_TASK.format() in commitments.prompt(0)
    assert _DECISION_TASK.format() in decisions.prompt(0)


def test_one_cited_request_still_takes_the_single_prompt() -> None:
    provider = Provider(json.dumps({"summary": summary(1), "used": [3]}, ensure_ascii=False))

    (out,) = resolver(provider).resolve_with_evidence([item(1)])

    assert out.text == summary(1)
    assert "항목" not in provider.prompt(0)


def test_purposes_never_share_a_call() -> None:
    provider = Provider()

    resolver(provider).resolve_with_evidence(
        [item(1), item(2, purpose="decision"), item(3), item(4, purpose="decision")]
    )

    assert len(provider.bodies) == 2
    assert "그 1번" in provider.prompt(0) and "그 3번" in provider.prompt(0)
    assert "그 2번" in provider.prompt(1) and "그 4번" in provider.prompt(1)


def test_what_a_batch_leaves_goes_to_the_second_model_in_one_more_call() -> None:
    unsound = "그 2번 버그는 [덧붙인 절] 제가 이번 빌드에 넣어 볼게요"
    provider = Provider(
        items((1, summary(1), [3]), (2, unsound, [3])),  # 3 skipped, 2 unsound
        items((1, summary(2), [3]), (2, summary(3), [3])),
    )

    out = resolver(provider, second="second").resolve_with_evidence([item(1), item(2), item(3)])

    assert provider.models == ["first", "second"]
    assert "그 1번" not in provider.prompt(1), "a resolved item is not asked again"
    assert [r.text for r in out] == [summary(1), summary(2), summary(3)]


def test_one_item_left_goes_to_the_second_model_on_the_single_prompt() -> None:
    provider = Provider(
        items((1, summary(1), [3])),
        json.dumps({"summary": summary(2), "used": [3]}, ensure_ascii=False),
    )

    out = resolver(provider, second="second").resolve_with_evidence([item(1), item(2)])

    assert provider.models == ["first", "second"]
    assert "항목" not in provider.prompt(1)
    assert [r.text for r in out] == [summary(1), summary(2)]


def test_without_a_second_model_what_a_batch_leaves_is_the_raw_quote() -> None:
    provider = Provider(items((2, summary(2), [3])))

    out = resolver(provider).resolve_with_evidence([item(1), item(2)])

    assert len(provider.bodies) == 1
    assert [r.text for r in out] == [item(1).target, summary(2)]
    assert out[0].used == ()


def test_a_failed_call_costs_its_items_their_rewrite_and_nothing_more() -> None:
    provider = Provider(RuntimeError("provider down"))

    out = resolver(provider, second="second").resolve_with_evidence([item(1), item(2)])

    assert len(provider.bodies) == 1
    assert [r.text for r in out] == [item(1).target, item(2).target]


def test_an_unreadable_answer_sends_every_item_to_the_second_model_once() -> None:
    provider = Provider("not json", items((1, summary(1), []), (2, summary(2), [])))

    out = resolver(provider, second="second").resolve_with_evidence([item(1), item(2)])

    assert provider.models == ["first", "second"]
    assert [r.text for r in out] == [summary(1), summary(2)]


def test_an_item_answered_twice_keeps_its_first_answer() -> None:
    provider = Provider(
        items((1, summary(1), [3]), (1, "엉뚱한 문장 [덧붙임]", []), (2, summary(2), [3]))
    )

    out = resolver(provider).resolve_with_evidence([item(1), item(2)])

    assert [r.text for r in out] == [summary(1), summary(2)]


def test_names_are_numbered_once_across_the_batch_and_never_leave() -> None:
    one = ResolutionRequest(
        target="그건 제가 김민경 님 대신 볼게요",
        context=("박재경 님이 로그 정리를 맡았었죠",),
        target_id="a",
        context_ids=("a0",),
    )
    two = ResolutionRequest(
        target="그 일정은 박재경 님하고 제가 맞출게요",
        context=("발표 일정이 아직 안 정해졌어요",),
        target_id="b",
        context_ids=("b0",),
    )
    provider = Provider(
        items(
            (1, "로그 정리는 제가 [사람2] 님 대신 볼게요", [1]),
            (2, "발표 일정은 [사람1] 님하고 제가 맞출게요", [1]),
        )
    )
    r = resolver(provider)
    r.use_roster(["박재경", "김민경"])

    out = r.resolve_with_evidence([one, two])

    sent = json.dumps(provider.bodies, ensure_ascii=False)
    assert "박재경" not in sent and "김민경" not in sent and "재경" not in sent
    assert provider.prompt(0).count("[사람1]") == 2, "the same person, the same number"
    assert [x.text for x in out] == [
        "로그 정리는 제가 김민경 님 대신 볼게요",
        "발표 일정은 박재경 님하고 제가 맞출게요",
    ]


def test_a_placeholder_borrowed_from_another_item_is_refused() -> None:
    one = ResolutionRequest(
        target="그건 제가 정리할게요",
        context=("회의록 초안이 아직이에요",),
        target_id="a",
        context_ids=("a0",),
    )
    two = ResolutionRequest(
        target="그 일정은 박재경 님하고 맞출게요",
        context=("발표 일정이 아직 안 정해졌어요",),
        target_id="b",
        context_ids=("b0",),
    )
    provider = Provider(items((1, "회의록 초안은 [사람1] 님과 제가 정리할게요", [1])))
    r = resolver(provider)
    r.use_roster(["박재경"])

    first, _ = r.resolve_with_evidence([one, two])

    assert first.text == one.target, "item 1 never mentioned [사람1]"


def test_batches_stay_inside_the_outbound_limit_and_the_cap() -> None:
    long_line = "고객 인터뷰에서 나온 요청 사항을 정리한 문서가 공유 폴더에 올라가 있어요 " * 3
    requests = [
        ResolutionRequest(
            target=f"그 {n}번 문서는 제가 볼게요",
            context=(long_line,) * 3,
            target_id=f"t{n}",
            context_ids=(f"a{n}", f"b{n}", f"c{n}"),
        )
        for n in range(20)
    ]
    provider = Provider()

    resolver(provider).resolve_with_evidence(requests)

    assert 1 < len(provider.bodies) < len(requests)
    for n in range(len(provider.bodies)):
        assert len(provider.prompt(n)) <= _PROMPT_BUDGET
        assert len(re.findall(r"^항목 \d+$", provider.prompt(n), re.M)) <= MAX_BATCH


def test_short_items_are_capped_at_max_batch_per_call() -> None:
    provider = Provider()

    resolver(provider).resolve_with_evidence([item(n) for n in range(MAX_BATCH + 1)])

    assert len(provider.bodies) == 2
    assert "항목" in provider.prompt(0) and "항목" not in provider.prompt(1)


def test_the_second_model_is_asked_at_most_max_escalations_times_across_batches() -> None:
    n = MAX_BATCH * (MAX_ESCALATIONS + 2)
    provider = Provider()  # every answer blank: nothing resolves

    out = resolver(provider, second="second").resolve_with_evidence([item(k) for k in range(n)])

    assert provider.models.count("second") == MAX_ESCALATIONS
    assert [r.text for r in out] == [item(k).target for k in range(n)]
