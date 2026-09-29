"""Single-utterance probes for the verifier: ``python -m autune_gap.eval --probe``.

The meeting-level harness says how precision moved; this says why, one
utterance at a time. Each probe runs through ``service._hear`` — the same code
path a meeting takes — once with the verifier off and once with it on, and the
report shows how triage filed it and whether the outcome meets the label.

No database: a probe is one utterance and a template, and ``_hear`` reads
neither topics nor rows. The report prints probe ids and item keys, never the
utterance text, for the reason ``__main__`` gives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files

from autune_gap import semantic, service, template, verification
from autune_gap.config import get_settings
from autune_gap.pipeline.registry import get_sentence_embedder

DEFAULT_PROBES = "verification_probes_v1.json"

ANY_ITEM = "*"


@dataclass(frozen=True)
class Probe:
    id: str
    template_key: str
    text: str
    must: frozenset[str]
    must_not: frozenset[str]
    """``{"*"}`` stands for every item of the template."""

    def forbidden(self, keys: frozenset[str]) -> frozenset[str]:
        if ANY_ITEM in self.must_not:
            return keys
        return self.must_not

    def passes(self, heard: frozenset[str], keys: frozenset[str]) -> bool:
        return self.must <= heard and not (heard & self.forbidden(keys))


def load_probes(name: str = DEFAULT_PROBES) -> list[Probe]:
    raw = json.loads((files(__package__) / "fixtures" / name).read_text(encoding="utf-8"))
    probes = []
    for entry in raw["probes"]:
        chosen = template.get_template(entry["template"])
        keys = {item.key for item in chosen.items}
        named = (set(entry["must"]) | set(entry["must_not"])) - {ANY_ITEM}
        unknown = sorted(named - keys)
        if unknown:
            raise ValueError(f"probe {entry['id']!r} names items {chosen.key!r} lacks: {unknown}")
        probes.append(
            Probe(
                id=entry["id"],
                template_key=chosen.key,
                text=entry["text"],
                must=frozenset(entry["must"]),
                must_not=frozenset(entry["must_not"]),
            )
        )
    return probes


@dataclass(frozen=True)
class ProbeResult:
    probe: Probe
    top: tuple[tuple[str, float], ...]
    """The embedder's three nearest classes, background included."""
    triage: str
    embedding: frozenset[str]
    """Heard with the verifier off."""
    verified: frozenset[str] | None
    """Heard with the verifier on, or ``None`` when no verifier was run."""
    keys: frozenset[str]


def run_probe(probe: Probe, *, with_verifier: bool) -> ProbeResult:
    """One probe through ``service._hear``. The caller has set the embedder and
    verifier implementations; ``with_verifier`` says whether one is set."""
    settings = get_settings()
    chosen = template.get_template(probe.template_key)
    keys = frozenset(item.key for item in chosen.items)

    embedder = get_sentence_embedder()
    if embedder is None:
        raise ValueError("probes need an embedder: --embedder local or fake")
    examples = semantic.examples_for(chosen)
    ranking = semantic.rank(
        embedder.embed([probe.text]), embedder.embed(list(examples.texts)), examples.labels
    )[0]
    decision = verification.triage(
        [ranking],
        verification.TriageThresholds(
            confident_score=settings.verify_confident_score,
            confident_lead=settings.verify_confident_lead,
            candidate_score=settings.verify_candidate_score,
            candidates=settings.verify_candidates,
        ),
    )[0]

    embedding_only = semantic.nearest_item(
        ranking, floor=settings.semantic_floor, margin=settings.semantic_margin
    )
    verified = service._hear(chosen, [probe.text], settings).heard if with_verifier else None

    return ProbeResult(
        probe=probe,
        top=ranking.classes[:3],
        triage=decision.triage.value,
        embedding=frozenset({embedding_only}) if embedding_only else frozenset(),
        verified=verified,
        keys=keys,
    )


def format_probes(results: list[ProbeResult], *, verifier: str) -> str:
    lines = [
        f"Verification probes -- {len(results)} utterances, verifier {verifier}",
        "",
        f"{'probe':<31}{'triage':<11}{'embedding':<22}{verifier:<22}label",
    ]
    for result in results:
        probe = result.probe
        label = f"must {sorted(probe.must)}" if probe.must else ""
        if probe.must_not:
            label += f" not {sorted(probe.must_not)}"
        embedding = _verdict(result.embedding, probe, result.keys)
        verified = "-" if result.verified is None else _verdict(result.verified, probe, result.keys)
        lines.append(f"{probe.id:<31}{result.triage:<11}{embedding:<22}{verified:<22}{label}")
        lines.append(
            "    nearest: " + ", ".join(f"{name} {score:.3f}" for name, score in result.top)
        )

    graded = [result for result in results if result.verified is not None]
    lines += [
        "",
        f"pass, embedding only: {sum(r.probe.passes(r.embedding, r.keys) for r in results)}"
        f"/{len(results)}",
    ]
    if graded:
        passed = sum(r.probe.passes(r.verified, r.keys) for r in graded if r.verified is not None)
        lines.append(f"pass, with {verifier}: {passed}/{len(graded)}")
    return "\n".join(lines)


def _verdict(heard: frozenset[str], probe: Probe, keys: frozenset[str]) -> str:
    mark = "ok" if probe.passes(heard, keys) else "WRONG"
    return f"{mark} {sorted(heard) or '[]'}"
