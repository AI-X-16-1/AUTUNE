"""Roster names leave for the model as ``[사람N]`` (#1162 review).

Module B sends its cloud model the team's names replaced by placeholders
(#411, #530), and #392's rule for a cloud model assumes it. Live research
sends live rows, questions, earlier meetings' quotes and the Google Search
query, so it does the same: every text a call sends is substituted, and each
``[사람N]`` in what comes back is put back as the form it stood for, since the
question and the note are read by the team.

The matching is B's (``autune_extraction.pipeline.llm``), copied rather than
imported: a module takes part in the agent layer only through its
``tools.py``. Keep the two in step. The roster is B's ``team_roster`` too: the
team's members, and the accounts on the meeting's own participant rows, so a
person who has left the team is still replaced in a meeting they were in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from autune_agent.main.gemini import WebAnswer
from autune_core import Meeting, Participant, TeamMember, User

PLACEHOLDER = "[사람{n}]"

_HANGUL_FULL_NAME = re.compile(r"[가-힣]{3}")
_HANGUL_WORD = re.compile(r"[가-힣]+")
_MARK = re.compile(r"\[사람\d+\]")


class Text(Protocol):
    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str: ...

    def search(self, instructions: str, question: str) -> WebAnswer: ...


def roster(session: Session, meeting_id: str) -> list[str]:
    """Display names not sent for this meeting: its team, and its participants."""
    on_the_team = (
        select(TeamMember.user_id)
        .join(Meeting, Meeting.team_id == TeamMember.team_id)
        .where(Meeting.id == meeting_id)
    )
    in_the_meeting = select(Participant.user_id).where(
        Participant.meeting_id == meeting_id, Participant.user_id.is_not(None)
    )
    return list(
        session.scalars(
            select(User.display_name)
            .where(or_(User.id.in_(on_the_team), User.id.in_(in_the_meeting)))
            .order_by(User.id)
        )
    )


def _variants(name: str) -> list[str]:
    """The forms a person is called by: the name, a spaced name joined and
    swapped, and a Korean given name. Over-matching is the cheap mistake;
    nothing shorter than two characters."""
    forms = [name]
    words = name.split()
    if len(words) >= 2 and all(_HANGUL_WORD.fullmatch(word) for word in words):
        joined = "".join(words)
        forms.append(joined)
        if len(words) == 2:
            first, second = words
            forms.append(second + first)
            if len(first) == 1 and len(second) >= 2:
                forms.append(second)
            elif len(second) == 1 and len(first) >= 2:
                forms.append(first)
        elif _HANGUL_FULL_NAME.fullmatch(joined):
            forms.append(joined[1:])
    elif _HANGUL_FULL_NAME.fullmatch(name):
        forms.append(name[1:])
    return [form for form in dict.fromkeys(forms) if len(form) >= 2]


def substitute(texts: Sequence[str], names: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """``texts`` with every roster name as ``[사람N]``, and what each stood for.

    Longest form first, so "김민경" never leaves "김[사람1]"; whatever follows a
    name stays. One person is one number across ``texts``, numbered by first
    appearance. A form two people share is its own person. No roster, no change.
    """
    owners: dict[str, set[str]] = {}
    for name in {" ".join(n.split()) for n in names if n and n.strip()}:
        for form in _variants(name):
            owners.setdefault(form, set()).add(name)
    if not owners:
        return list(texts), {}
    pattern = re.compile("|".join(re.escape(f) for f in sorted(owners, key=len, reverse=True)))
    person = {f: next(iter(p)) if len(p) == 1 else f"shared:{f}" for f, p in owners.items()}
    numbers: dict[str, int] = {}
    surface: dict[str, str] = {}

    def placeholder(match: re.Match[str]) -> str:
        marked = PLACEHOLDER.format(n=numbers.setdefault(person[match.group(0)], len(numbers) + 1))
        surface.setdefault(marked, match.group(0))
        return marked

    return [pattern.sub(placeholder, text) for text in texts], surface


def restore(text: str, surface: dict[str, str]) -> str:
    """Each ``[사람N]`` the model wrote back as the form it stood for."""
    return _MARK.sub(lambda m: surface.get(m.group(0), m.group(0)), text)


class NamedText:
    """A ``GeminiText`` that sends no roster name and answers with names."""

    def __init__(self, inner: Text, names: Sequence[str]) -> None:
        self._inner = inner
        self._names = list(names)

    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str:
        (sent,), surface = substitute([text], self._names)
        return restore(self._inner.generate(instructions, sent, json_answer=json_answer), surface)

    def search(self, instructions: str, question: str) -> WebAnswer:
        (sent,), surface = substitute([question], self._names)
        answer = self._inner.search(instructions, sent)
        return WebAnswer(text=restore(answer.text, surface), sources=answer.sources)
