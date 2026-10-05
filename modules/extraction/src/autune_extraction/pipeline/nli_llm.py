"""Step 4's entailment question answered by a cloud LLM (``nli_impl=llm``).

Step 4 (#12) asks of every utterance the classifier called ``ambiguous``
whether it entails "the speaker promised to do this". The local model for it
is klue/roberta on KorNLI, transferred zero-shot to meeting Korean; this asks
Gemini the same question instead (the user, 2026-10-05). Opt-in and never the
default, like every cloud implementation in this module, and refused at
start-up unless the deployment acknowledged #392 (``ExtractionSettings``).

**What leaves** is what ``LlmClassifier`` already sends about the same
utterances, and less of it: the masked text of the ``ambiguous`` ones only --
a handful a meeting -- with no speaker, no id, no meeting and no surrounding
lines; the team's names replaced by ``[사람N]`` (``use_roster``, set per
meeting by the task); and the hypothesis, which is a fixed sentence of ours.
Only consenting speakers' lines can be ``ambiguous`` at all: the classifier
never sees the others. Every request goes through ``HttpClient`` and its
outbound check.

**One call for a meeting's worth, usually.** The premises are numbered and
sent together under the outbound limit, so a meeting with a few ambiguous
agreements costs one request -- the free tier allows the default model twenty
a day. A premise too long for a request on its own is not sent.

**What an answer can do is small.** Step 4 only promotes: ``entailment``
turns an ``ambiguous`` row into a ``commitment``, and anything else leaves it
for the confirmation DM. So whatever is not a clear ``entailment`` -- a
missing number, a word that is not one of the three labels, a premise that
was not sent -- reads ``neutral``. A model that says nothing useful costs a
DM, never a commitment nobody made.

**Scores are not probabilities.** The model answers with a label; the label
gets ``LLM_CONFIDENCE`` and the other two share the rest, the way
``LlmClassifier`` reports its kinds. A promoted row's confidence is therefore
0.9 whatever the sentence -- not comparable with the local model's softmax,
which ``service.verify_utterances`` already says of the two it had.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from autune_core import get_logger
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .base import NliScores
from .llm import LLM_CONFIDENCE, GeminiClient, _answer_text, substitute_names

log = get_logger(__name__)

LABELS = ("entailment", "contradiction", "neutral")

_PROMPT = """\
다음은 회의에서 나온 발화입니다. 번호 하나가 발화 하나입니다. [사람N]은 가린 사람 이름이고, \
대괄호로 묶인 다른 표현은 가린 개인정보입니다.

{lines}

각 발화가 아래 가설을 뒷받침하는지 판정하세요.
가설: {hypothesis}

- entailment: 발화만 읽어도 가설이 참입니다. 화자가 자기 일로 받아 하겠다고 분명히 말했습니다.
- contradiction: 발화가 가설과 어긋납니다. 하지 않겠다고 했거나 다른 사람의 일이라고 했습니다.
- neutral: 발화만으로는 알 수 없습니다. 맞장구, 검토해 보겠다는 말, 조건이 붙은 말이 여기에 듭니다.
애매하면 neutral입니다.
예시(다른 회의): "네, 그건 제가 금요일까지 올릴게요." → entailment / \
"아, 네네 좋네요." → neutral / "한번 검토는 해 볼게요." → neutral / \
"그건 저희 팀 일이 아니라서 못 합니다." → contradiction
JSON 하나만 출력하세요: {{"labels": {{"1": "entailment", "2": "neutral"}}}}
"""
"""Unmeasured: written without a key that may see a real meeting, and scored on
nothing yet. The examples are analogues, not lines from any transcript. They
carry no ``[사람N]``, which a model could copy into an answer."""

_OVERHEAD = len(_PROMPT) + 200
"""The prompt's own text plus the request body's other strings."""


def _scores(label: str) -> NliScores:
    rest = (1.0 - LLM_CONFIDENCE) / 2
    weight = {name: (LLM_CONFIDENCE if name == label else rest) for name in LABELS}
    return NliScores(
        label=label,
        entailment=weight["entailment"],
        contradiction=weight["contradiction"],
        neutral=weight["neutral"],
    )


def batches(premises: Sequence[str], budget: int) -> list[list[int]]:
    """The indices of ``premises`` in runs whose rendered size fits ``budget``,
    in order. A premise longer than the budget on its own is in no run: the
    outbound check would refuse the request, and every other premise with it."""
    out: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, premise in enumerate(premises):
        cost = len(premise) + 8  # the number, ". " and the newline
        if cost > budget:
            continue
        if current and size + cost > budget:
            out.append(current)
            current, size = [], 0
        current.append(index)
        size += cost
    if current:
        out.append(current)
    return out


def parse(answer: str, count: int) -> dict[int, str]:
    """``{"labels": {"2": "entailment"}}`` -> ``{2: "entailment"}``, for numbers
    1..``count`` only. Prose around the JSON, a number that was not asked, a
    word that is not a label: dropped, not guessed at."""
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        return {}
    try:
        labels = json.loads(match.group(0)).get("labels", {})
    except (json.JSONDecodeError, AttributeError):
        return {}
    if not isinstance(labels, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in labels.items():
        number = str(key).strip()
        label = str(value).strip().lower()
        if number.isdigit() and 1 <= int(number) <= count and label in LABELS:
            out[int(number)] = label
    return out


class LlmNli(GeminiClient):
    """Gemini's ``generateContent`` over masked premises, a batch a request."""

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        """One result per pair, aligned to ``pairs``. A failed request and a
        privacy refusal are raised, as the other NLI implementations raise:
        step 4 says nothing about an utterance it could not ask about."""
        if not pairs:
            return []
        out = [_scores("neutral")] * len(pairs)
        by_hypothesis: dict[str, list[int]] = {}
        for index, (_premise, hypothesis) in enumerate(pairs):
            by_hypothesis.setdefault(hypothesis, []).append(index)
        calls = 0
        for hypothesis, indices in by_hypothesis.items():
            premises = substitute_names([pairs[i][0] for i in indices], self._roster)
            budget = MAX_OUTBOUND_CHARS - _OVERHEAD - len(hypothesis)
            for batch in batches(premises, budget):
                calls += 1
                lines = "\n".join(f"{n}. {premises[i]}" for n, i in enumerate(batch, start=1))
                answer = self._ask(_PROMPT.format(lines=lines, hypothesis=hypothesis), calls)
                for number, label in parse(answer, len(batch)).items():
                    out[indices[batch[number - 1]]] = _scores(label)
        # Counts only: the premises are utterances.
        log.info(
            "extraction_llm_nli_answered",
            pairs=len(pairs),
            calls=calls,
            entailed=sum(1 for score in out if score.label == "entailment"),
        )
        return out

    def _ask(self, prompt: str, index: int) -> str:
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        return _answer_text(self._post(body, index=index))
