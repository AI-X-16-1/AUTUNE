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

TEMPLATE_DIR = Path(__file__).parent / "templates"

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

    ``roles`` names the job roles that had to be in the conversation for the
    item to count as covered. It is read only when the roles are actually known:
    ``participants.role`` is never written today (#22, awaiting A), so the
    participation signal is dropped and the remaining weights renormalised
    rather than scored as if everybody were silent.
    """

    key: str
    category: str
    item: str
    weight: float
    keywords: tuple[str, ...]
    question: str
    roles: tuple[str, ...] = ()


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
        roles=tuple(str(role) for role in entry.get("roles", ())),
    )
