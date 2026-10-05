"""``python -m autune_extraction.eval.resolver_batch`` -- the cloud resolver with
items batched (#779) against the same resolver one item per call.

#779 packs a meeting's summaries into shared calls. Its rules and examples are
the measured single prompt's, word for word, but the batched framing was never
scored against the real API. This runs both on the same dummy meetings so it
can be: the number of calls each made, how many sentences each rewrote, how
often the two agree, and a file holding the two side by side for a person to
read.

**Dummy meetings only** (#392). It calls the provider through
``registry.get_resolver``, so every guard a worker has applies here too:
``AUTUNE_EXTRACTION_RESOLVER_IMPL=llm``, an API key, and
``AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392``. Nothing here gets around them.

Input, one JSON file::

    {"meetings": [{"name": "8.txt",
                   "roster": ["김민경", "박재경"],
                   "utterances": [{"id": "u1", "text": "...", "kind": "commitment"},
                                  {"id": "u2", "text": "...", "kind": null}]}]}

``kind`` is ``commitment``, ``decision`` or null -- the gold labels, or any
classifier's, since only the resolver is under test. ``roster`` is optional.

The console gets counts only, in ASCII (the reason ``eval.__main__`` gives).
The sentences go to ``--out``, a file the person running this chose.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autune_contracts.enums import UtteranceKind
from autune_extraction import service
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.pipeline import resolver as resolver_module
from autune_extraction.pipeline.base import Resolution, give_roster
from autune_extraction.pipeline.resolver import LlmResolver

MODES = ("single", "batch")


@dataclass(frozen=True)
class Meeting:
    name: str
    utterances: list[ClassifiedUtterance]
    roster: list[str] = field(default_factory=list)


@dataclass
class Run:
    """One mode over every meeting: what it wrote and what it cost."""

    calls: Counter[str] = field(default_factory=Counter)
    """Requests per model."""
    out: dict[str, Resolution] = field(default_factory=dict)
    """Each target's resolution, keyed ``<meeting>/<id>``."""


def load(path: Path) -> list[Meeting]:
    data = json.loads(path.read_text(encoding="utf-8"))
    meetings = []
    for m in data["meetings"]:
        utterances = [
            ClassifiedUtterance(
                id=str(u["id"]),
                kind=UtteranceKind(u["kind"]) if u.get("kind") else None,
                confidence=1.0,
                text=str(u["text"]),
                speaker=str(u.get("speaker", "")),
            )
            for u in m["utterances"]
        ]
        meetings.append(Meeting(str(m["name"]), utterances, list(m.get("roster", []))))
    return meetings


class _Counting:
    """The resolver's HTTP client, counting requests per model."""

    def __init__(self, inner: Any, calls: Counter[str]) -> None:
        self._inner = inner
        self._calls = calls

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._calls[path.split("/models/")[-1].split(":")[0]] += 1
        return self._inner.request(method, path, **kwargs)


@contextmanager
def _mode(resolver: LlmResolver, mode: str, calls: Counter[str]) -> Iterator[None]:
    """``single`` caps a batch at one request, which is the single prompt
    (``resolve_with_evidence`` sends a batch of one through ``_resolve_one``)."""
    inner = resolver._client  # noqa: SLF001 - counted, then put back
    saved = resolver_module.MAX_BATCH
    resolver._client = _Counting(inner, calls)  # noqa: SLF001
    if mode == "single":
        resolver_module.MAX_BATCH = 1
    try:
        yield
    finally:
        resolver_module.MAX_BATCH = saved
        resolver._client = inner  # noqa: SLF001


def run(resolver: LlmResolver, meetings: list[Meeting], mode: str) -> Run:
    result = Run()
    with _mode(resolver, mode, result.calls):
        for meeting in meetings:
            give_roster(resolver, meeting.roster)
            items = service.resolve_commitment_summaries(resolver, meeting.utterances)
            decisions = service.resolve_decision_summaries(
                resolver, meeting.utterances, meeting_id=meeting.name
            )
            for key, resolution in (*items.items(), *decisions.items()):
                result.out[f"{meeting.name}/{key}"] = resolution
    return result


def targets(meetings: list[Meeting]) -> dict[str, str]:
    """Each commitment's own text, keyed as ``Run.out`` is -- the raw quote a
    resolution that rewrote nothing returns."""
    return {
        f"{m.name}/{u.id}": u.text
        for m in meetings
        for u in m.utterances
        if u.kind is UtteranceKind.COMMITMENT
    }


def report(runs: dict[str, Run], raw: dict[str, str]) -> str:
    """Counts only. "rewritten" is over commitments, the ones whose raw quote is
    known here; a decision's write-up starts from an assembled line, so for
    decisions the side-by-side file is the comparison."""
    keys = sorted(set().union(*(r.out for r in runs.values())))
    items = [k for k in keys if k in raw]
    lines = [f"{len(items)} item summaries, {len(keys) - len(items)} decision write-ups", ""]
    lines.append(f"{'mode':<8}{'calls':>7}{'items rewritten':>17}  per model")
    for mode, r in runs.items():
        rewritten = sum(1 for k in items if k in r.out and r.out[k].text != raw[k])
        per_model = ", ".join(f"{m}={n}" for m, n in sorted(r.calls.items()))
        lines.append(f"{mode:<8}{sum(r.calls.values()):>7}{rewritten:>17}  {per_model}")
    if len(runs) == 2:
        a, b = runs.values()
        same = sum(1 for k in keys if k in a.out and k in b.out and a.out[k].text == b.out[k].text)
        lines += ["", f"same sentence in both modes: {same} of {len(keys)}"]
    return "\n".join(lines)


def side_by_side(runs: dict[str, Run], raw: dict[str, str]) -> list[dict[str, Any]]:
    keys = sorted(set().union(*(r.out for r in runs.values())))
    rows = []
    for key in keys:
        row: dict[str, Any] = {"key": key, "target": raw.get(key)}
        for mode, r in runs.items():
            resolution = r.out.get(key)
            row[mode] = resolution.text if resolution else None
            row[f"{mode}_used"] = list(resolution.used) if resolution else []
        rows.append(row)
    return rows


def main(
    argv: list[str] | None = None, *, resolver_factory: Callable[[], Any] | None = None
) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m autune_extraction.eval.resolver_batch")
    parser.add_argument("meetings", type=Path, help="the dummy meetings, as described above")
    parser.add_argument("--out", type=Path, required=True, help="where the sentences go")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args(argv)

    if resolver_factory is None:
        from autune_extraction.pipeline import registry  # noqa: PLC0415 - reads settings

        resolver_factory = registry.get_resolver
    resolver = resolver_factory()
    if not isinstance(resolver, LlmResolver):
        print("AUTUNE_EXTRACTION_RESOLVER_IMPL must be llm: this compares the cloud resolver.")
        return 2

    meetings = load(args.meetings)
    raw = targets(meetings)
    runs = {mode: run(resolver, meetings, mode) for mode in args.modes}
    args.out.write_text(
        json.dumps(side_by_side(runs, raw), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report(runs, raw))
    print(f"\nsentences side by side: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
