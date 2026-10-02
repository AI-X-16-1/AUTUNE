"""Domain templates — the checklist a meeting is compared against.

**Files in this package, not rows in a table.** A template is reference data:
it is not derived from a meeting, so it is the one thing module C holds that
cannot cascade from ``meetings.id``, and a table would have had to hang off
something — a team, or nothing — that nobody has decided yet (#22). As files
the question does not arise, git holds the history, and an edit goes through
PR review, which is the right control for content that decides gap precision.

``gap_templates`` stays unbuilt until a team writes its own template (Phase 2);
the anchor is obvious then, because a real user flow names it.

Nothing here reads the database or a meeting. ``service`` picks which template
applies and ``detect`` compares against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from autune_core.errors import ConfigurationError, ValidationError
from autune_gap.pipeline.base import RELATION_LABELS

TEMPLATE_DIR = Path(__file__).parent / "templates"

TOPIC_PLACEHOLDER = "{topic}"

VARIABLE_PARTICLES: tuple[str, ...] = ("은", "는", "이", "가", "을", "를", "과", "와")
"""Korean particles whose form depends on whether the noun before them ends in
a consonant — 캐시**는** but 검색 기능**은**.

A topic label is a noun this module read out of a meeting, so which form is
correct is not knowable when the question is written. Rather than pick the
particle at runtime, ``question_about`` is required to follow ``{topic}`` with
something invariant: ``의``, ``에``, ``에서``, ``에 대해``. The rule is enforced
at load because the failure is a product screen showing "캐시은", and copy is
the kind of thing somebody improves without knowing why it was phrased that
way."""

KEYWORD_MIN = 2
"""Shorter than this matches by accident. Enforced at load so a bad template
fails on the first call rather than by quietly raising a gap in every meeting."""


@dataclass(frozen=True)
class TemplateItem:
    """One thing the meeting was supposed to settle.

    ``item`` and ``question`` are Korean product copy — they reach S20 and
    ``gap_gaps`` — and every field name around them is English.

    ``keywords`` decide whether the meeting touched this at all; ``weight`` is
    how much its absence matters, and it is the only risk input a template
    author controls.

    Two questions, because a gap raised on a topic the meeting named and a gap
    raised on nothing are different questions to ask. ``question`` is the
    generic one; ``question_about`` names the topic and is used only when there
    is one to name (#35). See ``detect.Finding``.

    **There is no ``roles`` field.** #14 wants "a topic no engineer spoke on is
    riskier" and the data for it does not exist: ``participants.role`` is
    written by no production code (#22). A field template authors could fill
    would invite them to state a rule nothing enforces, so it arrives with the
    rule rather than before it.
    """

    key: str
    category: str
    item: str
    weight: float
    keywords: tuple[str, ...]
    question: str
    question_about: str
    """The same question with ``{topic}`` in it, for a partial finding."""
    ask_about_subject: bool = False
    """Whether ``question_about`` may name the meeting's subject when no topic
    matched the item (``detect.question_for``). An author's call per item:
    "{topic}의 성능 목표는?" reads as one question about the feature the meeting
    was about, while "{topic}에 대해 이 회의 다음에 일어나는 일은?" pins a
    question about the whole meeting onto one of its topics. Off unless the
    template says so."""
    relations: tuple[str, ...] = ()
    """Relations whose presence in the graph is the meeting having raised this
    item — ``depends_on`` for the dependency item.

    Some items are about how two things stand to each other rather than about a
    thing, and no topic label carries that. "마이그레이션 검증 스크립트가 먼저
    있어야 롤백 절차가 의미가 있습니다" is a dependency stated outright, and the
    rules already read it as ``롤백 절차 depends_on 마이그레이션 검증 스크립트``
    — but neither label contains 의존 or 선행, so the item came back missing
    over a graph that held the answer. A topic at either end of an edge named
    here matches the item the way a keyword hit does, and ``detect.classify``
    reads its centrality as it reads any match.

    Only step 2's relations, never ``co_occurs``: two topics in one utterance
    say nothing about how they relate, and an item matched on proximity would
    be covered by any meeting that named two things at once. Optional — an item
    without it is matched by its keywords alone."""
    examples: tuple[str, ...] = ()
    """Sentences a meeting says when it settles this item — "민수님이 담당해서
    수요일까지 마무리해 주세요" for ownership. Read by ``semantic`` through a
    sentence embedder, so an item settled with a verb and a date is heard even
    when no keyword was said. Optional: an item without them is matched by its
    keywords alone, and the embedder is off by default."""


@dataclass(frozen=True)
class Template:
    """One domain template, already merged with whatever it extends."""

    key: str
    name: str
    version: str
    """Every file that contributed, in the order they were merged —
    ``general.1`` or ``general.1+feature_planning.1``.

    A composite rather than one number because a template that extends another
    changes when its parent changes. Recorded on every gap raised, so precision
    measured across an edit does not average two different checklists together.
    """

    items: tuple[TemplateItem, ...]


def get_template(key: str) -> Template:
    """The template by key, or ``ValidationError`` naming what does exist.

    A 422 rather than a 404: the key arrives from a request body or a stored
    override, and what is wrong is the value, not the address.
    """
    templates = load_templates()
    template = templates.get(key)
    if template is None:
        raise ValidationError(
            f"unknown template {key!r}; known: {sorted(templates)}", field="template_key"
        )
    return template


def available() -> list[Template]:
    """Every template, by key. What ``GET /api/gap/templates`` lists."""
    templates = load_templates()
    return [templates[key] for key in sorted(templates)]


@lru_cache
def load_templates() -> dict[str, Template]:
    """Read and merge every template file. Cached — these do not change at
    runtime, and a meeting reads them on every pipeline pass."""
    raw = {path.stem: _read(path) for path in sorted(TEMPLATE_DIR.glob("*.yaml"))}
    if not raw:
        raise ConfigurationError(f"no domain templates found in {TEMPLATE_DIR.name}/")
    return {key: _resolve(key, raw, seen=()) for key in raw}


def _read(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ConfigurationError(f"template {path.name} is not a mapping")
    if document.get("key") != path.stem:
        # The filename is what `extends` and the stored override both name, so
        # a `key` that disagrees with it would resolve to two different things
        # depending on which one the caller had.
        raise ConfigurationError(f"template {path.name} declares key {document.get('key')!r}")
    return document


def _resolve(key: str, raw: dict[str, dict[str, Any]], seen: tuple[str, ...]) -> Template:
    """One template with its parent's items in front of its own.

    Recursive so a template may extend one that extends another. ``seen`` is
    carried rather than a visited set because the cycle that matters is a
    template reaching itself through its own ancestry, and the chain is what
    names it in the error.
    """
    if key in seen:
        raise ConfigurationError(f"template {key!r} extends itself: {' -> '.join([*seen, key])}")
    if key not in raw:
        raise ConfigurationError(f"template {seen[-1] if seen else '?'} extends unknown {key!r}")

    document = raw[key]
    parent_key = document.get("extends")
    parent = _resolve(parent_key, raw, (*seen, key)) if parent_key else None

    items = tuple(_item(entry, key) for entry in document.get("items", ()))
    own = f"{key}.{document['version']}"

    if parent is None:
        return Template(key=key, name=document["name"], version=own, items=items)

    inherited = {item.key for item in parent.items}
    clash = sorted(item.key for item in items if item.key in inherited)
    if clash:
        # Two items with one key would give two gaps the same natural identity,
        # and `service` keys a re-run's rows on exactly that.
        raise ConfigurationError(f"template {key!r} redefines inherited items: {clash}")

    return Template(
        key=key,
        name=document["name"],
        version=f"{parent.version}+{own}",
        items=(*parent.items, *items),
    )


def _question_about(entry: dict[str, Any], template_key: str) -> str:
    """The topic-naming question, checked for the two ways it silently fails."""
    where = f"template {template_key!r} item {entry.get('key')!r}"
    question = str(entry["question_about"])

    if TOPIC_PLACEHOLDER not in question:
        raise ConfigurationError(
            f"{where} has a question_about with no {TOPIC_PLACEHOLDER} in it, so it would "
            "read exactly like the generic question and name nothing"
        )

    after = question.split(TOPIC_PLACEHOLDER, 1)[1][:1]
    if after in VARIABLE_PARTICLES:
        raise ConfigurationError(
            f"{where} follows {TOPIC_PLACEHOLDER} with {after!r}, a particle whose form "
            f"depends on the topic's last syllable. Use an invariant one: 의, 에, 에서, 에 대해"
        )
    return question


def _item(entry: dict[str, Any], template_key: str) -> TemplateItem:
    keywords = tuple(str(word).strip().casefold() for word in entry.get("keywords", ()))
    short = sorted(word for word in keywords if len(word) < KEYWORD_MIN)
    if short:
        raise ConfigurationError(
            f"template {template_key!r} item {entry.get('key')!r} has keywords shorter than "
            f"{KEYWORD_MIN} characters: {short}"
        )
    if not keywords:
        raise ConfigurationError(
            f"template {template_key!r} item {entry.get('key')!r} has no keywords, so nothing "
            "could ever match it"
        )

    weight = float(entry["weight"])
    if not 0 < weight <= 1:
        raise ConfigurationError(
            f"template {template_key!r} item {entry.get('key')!r} has weight {weight}, "
            "which is outside (0, 1]"
        )

    return TemplateItem(
        key=str(entry["key"]),
        category=str(entry["category"]),
        item=str(entry["item"]),
        weight=weight,
        keywords=keywords,
        question=str(entry["question"]),
        question_about=_question_about(entry, template_key),
        ask_about_subject=_flag(entry, template_key, "ask_about_subject"),
        relations=_relations(entry, template_key),
        examples=_examples(entry, template_key),
    )


def _flag(entry: dict[str, Any], template_key: str, name: str) -> bool:
    value = entry.get(name, False)
    if not isinstance(value, bool):
        # "false" in quotes is a string, and a non-empty string is truthy.
        raise ConfigurationError(
            f"template {template_key!r} item {entry.get('key')!r} has {name} "
            f"{value!r}, which is not true or false"
        )
    return value


def _relations(entry: dict[str, Any], template_key: str) -> tuple[str, ...]:
    where = f"template {template_key!r} item {entry.get('key')!r}"
    raw = entry.get("relations", ())
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        # A bare string would be iterated character by character, and no
        # character is a relation — the item would silently match nothing.
        raise ConfigurationError(f"{where} has relations that are not a list")
    relations = tuple(str(name).strip() for name in raw)
    unknown = sorted(name for name in relations if name not in RELATION_LABELS)
    if unknown:
        # ``co_occurs`` lands here too, and is meant to: it is not a relation
        # anybody asserted. See ``TemplateItem.relations``.
        raise ConfigurationError(
            f"{where} names relations step 2 does not extract: {unknown}; "
            f"known: {list(RELATION_LABELS)}"
        )
    return relations


def _examples(entry: dict[str, Any], template_key: str) -> tuple[str, ...]:
    raw = entry.get("examples", ())
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        # A bare string would be iterated character by character, and every
        # character would become an example sentence.
        raise ConfigurationError(
            f"template {template_key!r} item {entry.get('key')!r} has examples that are not a list"
        )
    examples = tuple(str(sentence).strip() for sentence in raw)
    if any(not sentence for sentence in examples):
        raise ConfigurationError(
            f"template {template_key!r} item {entry.get('key')!r} has an empty example sentence"
        )
    return examples
