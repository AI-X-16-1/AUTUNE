"""Running an evaluation suite under a chosen ``engine_mode``, and comparing two runs.

``get_settings`` and the model getters are cached per process, so switching mode
means changing the environment *and* dropping both caches, and undoing it after.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

from autune_context.config import get_settings
from autune_context.pipeline import get_llm, reset_cache

_ENV = "AUTUNE_CONTEXT_ENGINE_MODE"


@contextmanager
def engine_mode(mode: str) -> Generator[None]:
    """Run the block with ``AUTUNE_CONTEXT_ENGINE_MODE=mode``, then restore."""
    previous = os.environ.get(_ENV)
    os.environ[_ENV] = mode
    get_settings.cache_clear()
    reset_cache()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = previous
        get_settings.cache_clear()
        reset_cache()


def llm_usage_line() -> str:
    """What the LLM spent in the mode that just ran. Call inside ``engine_mode``,
    before the caches are dropped."""
    usage = get_llm().usage
    return (
        f"llm: {usage.calls} calls, {usage.input_tokens} input / {usage.output_tokens} "
        f"output tokens, {usage.seconds:.0f}s of request time, "
        f"{usage.unjudged} unusable answers (counted as 'not the same')"
    )


def comparison(suite: str, runs: dict[str, tuple[dict[str, float], dict[str, bool]]]) -> str:
    """Side by side: each headline metric per mode, then the cases each mode
    got right that the first one (the baseline) did not, and the reverse.

    ``runs`` maps a mode name to ``(headline metrics, {case: correct})``; the
    first entry is the baseline the others are read against.
    """
    (base_name, (base_metrics, base_correct)), *others = runs.items()
    width = max(len(name) for name in base_metrics)
    names = [base_name, *(name for name, _ in others)]
    lines = [
        f"=== {suite}: {' vs '.join(names)} ===",
        f"  {'':<{width}}" + "".join(f"  {n:>8}" for n in names),
        *(
            f"  {metric:<{width}}"
            + "".join(f"  {metrics[metric]:>8.3f}" for metrics, _ in runs.values())
            for metric in base_metrics
        ),
    ]
    for name, (_, correct) in others:
        gained = sorted(c for c in base_correct if correct.get(c) and not base_correct[c])
        lost = sorted(c for c in base_correct if base_correct[c] and not correct.get(c))
        lines += [
            "",
            f"  {name} vs {base_name}: right where {base_name} was wrong ({len(gained)}): "
            f"{', '.join(gained) or '-'}",
            f"  {name} vs {base_name}: wrong where {base_name} was right ({len(lost)}): "
            f"{', '.join(lost) or '-'}",
        ]
    return "\n".join(lines)
