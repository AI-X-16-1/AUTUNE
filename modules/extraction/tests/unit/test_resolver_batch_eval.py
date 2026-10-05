"""``eval.resolver_batch``: both modes over the same meetings, counted and side by side.

No network: a fake provider answers like Gemini and records which form of the
prompt it was sent. The rules under test: ``single`` really sends one item per
call, the console report holds counts and never a sentence, and the module's
batch cap is put back however the run ends.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from autune_extraction.eval import resolver_batch
from autune_extraction.pipeline import resolver as resolver_module
from autune_extraction.pipeline.resolver import LlmResolver

TARGETS = [f"그 {n}번 버그는 제가 이번 빌드에 넣어 볼게요" for n in range(3)]


class Provider:
    """Answers every request with the raw quote(s) it was asked about."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        prompt = json["contents"][0]["parts"][0]["text"]
        self.prompts.append(prompt)
        # The lines under test, not a target the related lines also offer.
        asked = re.findall(r"^\d+ \[대상\] (.+)$", prompt, re.M)
        if "항목 1" in prompt:
            text = _dumps(
                {"items": [{"item": k, "summary": t, "used": []} for k, t in enumerate(asked, 1)]}
            )
        else:
            text = _dumps({"summary": asked[0] if asked else "", "used": []})
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def meetings_file(tmp_path: Path) -> Path:
    utterances = []
    for n, target in enumerate(TARGETS):
        utterances.append({"id": f"c{n}", "text": "네 그렇죠", "kind": None})
        utterances.append({"id": f"t{n}", "text": target, "kind": "commitment"})
    path = tmp_path / "meetings.json"
    path.write_text(_dumps({"meetings": [{"name": "m1", "utterances": utterances}]}), "utf-8")
    return path


def resolver(provider: Provider) -> LlmResolver:
    r = LlmResolver(api_key="k", model="first", base_url="http://llm.invalid")
    r._client = provider  # type: ignore[assignment]
    return r


def test_single_sends_one_item_per_call_and_batch_shares_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = Provider()
    out = tmp_path / "side.json"

    code = resolver_batch.main(
        [str(meetings_file(tmp_path)), "--out", str(out)],
        resolver_factory=lambda: resolver(provider),
    )

    assert code == 0
    single = [p for p in provider.prompts if "항목 1" not in p]
    batched = [p for p in provider.prompts if "항목 1" in p]
    assert len(single) == 3 and len(batched) == 1
    printed = capsys.readouterr().out
    assert "single        3" in printed and "batch         1" in printed
    assert "same sentence in both modes: 3 of 3" in printed
    assert not any(t in printed for t in TARGETS), "the console gets counts, never a sentence"
    rows = json.loads(out.read_text("utf-8"))
    assert [r["single"] for r in rows] == TARGETS and [r["batch"] for r in rows] == TARGETS


def test_the_batch_cap_is_put_back_even_when_a_run_fails(tmp_path: Path) -> None:
    class Broken(Provider):
        def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
            raise KeyboardInterrupt

    before = resolver_module.MAX_BATCH
    with pytest.raises(KeyboardInterrupt):
        resolver_batch.main(
            [str(meetings_file(tmp_path)), "--out", str(tmp_path / "o.json"), "--modes", "single"],
            resolver_factory=lambda: resolver(Broken()),
        )

    assert before == resolver_module.MAX_BATCH


def test_a_resolver_that_is_not_the_cloud_one_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = resolver_batch.main(
        [str(meetings_file(tmp_path)), "--out", str(tmp_path / "o.json")],
        resolver_factory=object,
    )

    assert code == 2
    assert "RESOLVER_IMPL must be llm" in capsys.readouterr().out
