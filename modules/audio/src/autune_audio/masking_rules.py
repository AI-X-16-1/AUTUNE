"""A team's own masking rules, learned from the misses its members reported (S30, #555).

The built-in masker knows phone numbers, e-mail, resident numbers, accounts and
cards. A company's employee number (`A-20391`) or ticket id is none of those,
and it keeps being missed in every meeting until somebody reports it. S30's
"이 패턴을 워크스페이스 마스킹 규칙에 추가" turns one report into a rule that
masks the same *shape* in every later transcript of that team.

**A rule is a shape, never the text.** The reported span is reduced to
character classes -- a digit is `#`, a Latin capital `A`, a small letter `a`,
a Hangul syllable `가` -- with separators kept: `A-20391` becomes `A-#####`.
That string is all that is stored, so a rule table holds nothing a reader
could recover the reported value from (privacy.md section 2).

**Only shapes that carry a digit and a Latin letter, and at least four
classed characters.** A shape of letters alone is a word shape: `가가가` is
every three-syllable word in Korean, and a rule built from a reported name
would mask whole transcripts -- names stay the recogniser's job. A shape of
digits and separators alone is a number shape: `####` is every year, price and
head count, `####-##-##` every date and `##:##` every time, and module B reads
due dates out of exactly that text (review of #612). An employee number or a
ticket id carries a letter; a purely numeric one cannot become a rule, which
is the price of not masking every number a team says. The floor of four keeps
`A-#` from eating every short code.

**A trailing Hangul run is dropped first.** A reporter selects `A-20391로` as
often as `A-20391`; the particle (or a unit, `원`, `명`) is not part of the
value, and a shape that kept it would only ever match with that particle.

**A match must stand on its own.** It may not touch another letter or digit on
either side, so `A-#####` does not mask the middle of `XA-203915`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, User, get_logger
from autune_core.errors import NotFoundError

from .masking import hide_reported
from .models import AudMaskingRule
from .schemas import MaskingRule
from .service import require_team_member

log = get_logger(__name__)

_CLASSES: tuple[tuple[str, str, str], ...] = (
    # (placeholder, test, regex class)
    ("#", "0123456789", r"\d"),
    ("A", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "[A-Z]"),
    ("a", "abcdefghijklmnopqrstuvwxyz", "[a-z]"),
)
_SEPARATORS = "-._/:"
MIN_CLASSED = 4
MAX_SHAPE = 64


def _placeholder(char: str) -> str | None:
    for placeholder, members, _ in _CLASSES:
        if char in members:
            return placeholder
    if "가" <= char <= "힣":
        return "가"
    return None


def shape_of(span: str) -> str | None:
    """The span as a shape, or ``None`` when it should not become a rule.

    ``None`` for: anything with a whitespace or a character that is neither a
    classed one nor a separator (a rule is one token); no digit; no Latin
    letter; fewer than ``MIN_CLASSED`` classed characters; or longer than a
    column holds. A trailing Hangul run is removed before any of that.
    """
    value = span.strip()
    while value and "가" <= value[-1] <= "힣":
        value = value[:-1]
    out: list[str] = []
    for char in value:
        placeholder = _placeholder(char)
        if placeholder is not None:
            out.append(placeholder)
        elif char in _SEPARATORS:
            out.append(char)
        else:
            return None
    shape = "".join(out)
    classed = sum(1 for c in shape if c not in _SEPARATORS)
    has_letter = "A" in shape or "a" in shape
    if "#" not in shape or not has_letter or classed < MIN_CLASSED or len(shape) > MAX_SHAPE:
        return None
    return shape


def _regex(shape: str) -> re.Pattern[str]:
    regex_of = {p: rx for p, _, rx in _CLASSES} | {"가": "[가-힣]"}
    parts: list[str] = []
    for run in re.finditer(r"(.)\1*", shape):
        char, count = run.group(1), len(run.group(0))
        piece = regex_of.get(char, re.escape(char))
        parts.append(piece if count == 1 else f"{piece}{{{count}}}")
    # Not glued to another letter or digit on either side. Hangul is left out
    # of the guard on purpose: a Korean particle follows a value directly
    # (`A-20391로`), and must not stop it from matching.
    return re.compile(rf"(?<![0-9A-Za-z]){''.join(parts)}(?![0-9A-Za-z])")


def apply(text: str, shapes: Iterable[str]) -> str:
    """``text`` with every match of every shape masked as a reported span is."""
    for shape in shapes:
        text = _regex(shape).sub(lambda m: hide_reported(m.group(0)), text)
    return text


def shapes_for_team(session: Session, team_id: str) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            sa.select(AudMaskingRule.shape)
            .where(AudMaskingRule.team_id == team_id)
            .order_by(AudMaskingRule.id)
        )
    )


def shapes_for_meeting(session: Session, meeting_id: str) -> tuple[str, ...]:
    team_id = session.scalar(sa.select(Meeting.team_id).where(Meeting.id == meeting_id))
    return shapes_for_team(session, team_id) if team_id else ()


def list_rules(session: Session, *, team_id: str, reader: User) -> list[MaskingRule]:
    require_team_member(session, user_id=reader.id, team_id=team_id)
    rows = session.scalars(
        sa.select(AudMaskingRule)
        .where(AudMaskingRule.team_id == team_id)
        .order_by(AudMaskingRule.id)
    )
    return [
        MaskingRule(id=r.id, shape=r.shape, category=r.category, created_at=r.created_at)
        for r in rows
    ]


def delete_rule(session: Session, *, team_id: str, rule_id: int, by: User) -> None:
    """Stop masking a shape in *later* transcripts. What it already masked stays
    masked: the text it replaced was never stored."""
    require_team_member(session, user_id=by.id, team_id=team_id)
    result = session.execute(
        sa.delete(AudMaskingRule).where(
            AudMaskingRule.id == rule_id, AudMaskingRule.team_id == team_id
        )
    )
    if not getattr(result, "rowcount", 0):
        raise NotFoundError("masking_rule", str(rule_id))
    log.info("audio_masking_rule_deleted", team_id=team_id, rule_id=rule_id, by=by.id)
