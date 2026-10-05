"""A team's projects, and which project each decision and item of a meeting is about.

Asked for by the user (2026-10-04): one meeting often covers several projects,
and what it settled should be summarised and sent out project by project. A
team lists its projects (``ext_projects``) with the other names people say for
them; ``assign`` reads what was said and points each decision and item at one.

**Rules, not a model.** Nothing leaves our infrastructure, so it runs on real
meetings whatever #392 decides. A person corrects what the rules get wrong on
the 요약 tab, and a project a person chose is never changed by them again
(``project_by_person``). In order:

1. A team with one project: everything is that project.
2. The row's own lines name exactly one project: that one.
3. Otherwise the topic in force: the latest line before the row's first line,
   within ``LOOKBACK`` lines, that names exactly one project. Meetings move
   from one project to the next ("이제 오튠 얘기할게요"), and what follows
   belongs to it until another is named. A line naming two projects at once
   settles nothing, and the search stops there rather than reaching past it.
4. Otherwise none -- "미분류" on the tab, for a person to choose.

Only consented speakers' lines are read, the same lines the classifier reads
(privacy.md section 5).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Meeting, TeamMember, User, get_logger
from autune_core.errors import ConflictError, NotFoundError, ValidationError

from . import project_send, service
from .models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtDecision,
    ExtDecisionSource,
    ExtProject,
)
from .pipeline.llm import substitute_names

log = get_logger(__name__)

LOOKBACK = 40
"""How many lines back the topic in force may have been named. Far enough for
a stretch of discussion on one project; not so far that a project named at the
start of an hour-long meeting claims everything after it."""

MAX_PROJECTS = 30
MAX_ALIASES = 10
MAX_ALIAS_CHARS = 50


@dataclass(frozen=True)
class Project:
    id: str
    names: tuple[str, ...]
    """The name and the aliases, as matched."""


def _pattern(name: str) -> re.Pattern[str]:
    """A name as it may be said: case ignored, and a Latin name only as a whole
    word ("app" must not match "happy"). Korean is matched as written, since a
    particle follows it directly ("오튠은", "오튠에서")."""
    escaped = re.escape(name)
    if re.fullmatch(r"[A-Za-z0-9 ._-]+", name):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


class Matcher:
    """Which projects a line names."""

    def __init__(self, projects: Sequence[Project]) -> None:
        self._patterns = [
            (project.id, [_pattern(n) for n in project.names if n.strip()]) for project in projects
        ]

    def named(self, text: str) -> set[str]:
        return {pid for pid, patterns in self._patterns if any(p.search(text) for p in patterns)}


def choose(
    projects: Sequence[Project],
    lines: Sequence[tuple[str, str]],
    row_lines: Iterable[str],
) -> str | None:
    """The project one row is about, by the rules in the module docstring.

    ``lines`` is the meeting as ``(utterance id, text)`` in spoken order;
    ``row_lines`` the ids of the lines the row was drawn from."""
    if not projects:
        return None
    if len(projects) == 1:
        return projects[0].id
    matcher = Matcher(projects)
    own = set(row_lines)
    text = {uid: t for uid, t in lines}
    in_own = set().union(*(matcher.named(text[uid]) for uid in own if uid in text))
    if len(in_own) == 1:
        return next(iter(in_own))
    positions = [i for i, (uid, _) in enumerate(lines) if uid in own]
    if not positions:
        return None
    first = min(positions)
    for i in range(first - 1, max(-1, first - 1 - LOOKBACK), -1):
        named = matcher.named(lines[i][1])
        if len(named) == 1:
            return next(iter(named))
        if len(named) > 1:
            return None
    return None


def team_projects(session: Session, team_id: str) -> list[ExtProject]:
    return list(
        session.scalars(
            select(ExtProject)
            .where(ExtProject.team_id == team_id)
            .order_by(ExtProject.created_at, ExtProject.id)
        )
    )


def reader_projects(session: Session, user_id: str) -> list[ExtProject]:
    """The projects of every team ``user_id`` is on, team by team."""
    return list(
        session.scalars(
            select(ExtProject)
            .join(TeamMember, TeamMember.team_id == ExtProject.team_id)
            .where(TeamMember.user_id == user_id)
            .order_by(ExtProject.team_id, ExtProject.created_at, ExtProject.id)
        )
    )


def _as_projects(rows: Sequence[ExtProject]) -> list[Project]:
    return [
        Project(row.id, (row.name, *[a for a in row.aliases.split("\n") if a.strip()]))
        for row in rows
    ]


def assign_meeting(session: Session, meeting_id: str) -> int:
    """Point every decision and item of the meeting that no person has placed
    at the project the rules choose. Returns how many changed. Safe to repeat."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return 0
    projects = _as_projects(team_projects(session, meeting.team_id))
    consented = service.consented_utterance_ids(session, meeting_id)
    lines = [
        (u.id, u.text)
        for u in service.stored_transcript(session, meeting_id)
        if u.id in consented and u.text
    ]
    changed = 0
    for item in session.scalars(
        select(ExtActionItem).where(
            ExtActionItem.meeting_id == meeting_id, ExtActionItem.project_by_person.is_(False)
        )
    ):
        sources = session.scalars(
            select(ExtActionItemSource.utterance_id).where(
                ExtActionItemSource.action_item_id == item.id
            )
        )
        chosen = choose(projects, lines, [s for s in sources if s])
        if chosen != item.project_id:
            item.project_id = chosen
            changed += 1
    for decision in session.scalars(
        select(ExtDecision).where(
            ExtDecision.meeting_id == meeting_id, ExtDecision.project_by_person.is_(False)
        )
    ):
        sources = session.scalars(
            select(ExtDecisionSource.utterance_id).where(
                ExtDecisionSource.decision_id == decision.id
            )
        )
        chosen = choose(projects, lines, [s for s in sources if s])
        if chosen != decision.project_id:
            decision.project_id = chosen
            changed += 1
    session.flush()
    log.info("extraction_projects_assigned", meeting_id=meeting_id, changed=changed)
    return changed


def _clean_aliases(aliases: Sequence[str], name: str) -> str:
    kept: list[str] = []
    for alias in aliases:
        text = " ".join(alias.split())
        if not text or text == name or text in kept:
            continue
        if len(text) > MAX_ALIAS_CHARS:
            raise ValidationError(
                f"an alias is at most {MAX_ALIAS_CHARS} characters", field="aliases"
            )
        kept.append(text)
    if len(kept) > MAX_ALIASES:
        raise ValidationError(f"at most {MAX_ALIASES} aliases", field="aliases")
    return "\n".join(kept)


def save_project(
    session: Session,
    team_id: str,
    *,
    name: str,
    aliases: Sequence[str],
    jira_project_key: str | None,
    project_id: str | None = None,
) -> ExtProject:
    """Create a project, or rename and re-alias one of this team's."""
    clean = " ".join(name.split())
    if not clean:
        raise ValidationError("a project needs a name", field="name")
    key = (jira_project_key or "").strip().upper() or None
    if key is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", key):
        raise ValidationError("not a Jira project key", field="jira_project_key")
    taken = session.scalar(
        select(ExtProject.id).where(
            ExtProject.team_id == team_id,
            ExtProject.name == clean,
            ExtProject.id != (project_id or ""),
        )
    )
    if taken is not None:
        raise ConflictError("the team already has a project of that name")
    if project_id is None:
        if len(team_projects(session, team_id)) >= MAX_PROJECTS:
            raise ValidationError(f"a team has at most {MAX_PROJECTS} projects", field="name")
        row = ExtProject(team_id=team_id)
    else:
        found = session.get(ExtProject, project_id)
        if found is None or found.team_id != team_id:
            raise NotFoundError("project", project_id)
        row = found
    row.name = clean
    row.aliases = _clean_aliases(aliases, clean)
    row.jira_project_key = key
    session.add(row)
    # Two members adding the same name at once: the unique constraint answers
    # the second, as a conflict rather than a 500.
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise ConflictError("the team already has a project of that name") from exc
    return row


def delete_project(session: Session, team_id: str, project_id: str) -> None:
    """Delete one of this team's projects; its rows become unassigned, and the
    copies of its minutes are queued to be taken out of the team's tools."""
    row = session.get(ExtProject, project_id)
    if row is None or row.team_id != team_id:
        raise NotFoundError("project", project_id)
    # Its minutes' copies go with it -- the team's tools and people's own
    # calendars -- queued before the rows cascade away.
    project_send.queue_project(session, project_id)
    for item in session.scalars(
        select(ExtActionItem).where(ExtActionItem.project_id == project_id)
    ):
        item.project_id = None
        item.project_by_person = False
    for decision in session.scalars(
        select(ExtDecision).where(ExtDecision.project_id == project_id)
    ):
        decision.project_id = None
        decision.project_by_person = False
    session.delete(row)
    session.flush()


def place(session: Session, row: ExtActionItem | ExtDecision, project_id: str | None) -> None:
    """A person puts a decision or an item in a project of its meeting's team --
    or in none. Either way the rules leave it there from now on, and so does
    extracting the meeting again."""
    if project_id is not None:
        project = session.get(ExtProject, project_id)
        meeting = session.get(Meeting, row.meeting_id)
        if project is None or meeting is None or project.team_id != meeting.team_id:
            raise ValidationError("not a project of this meeting's team", field="project_id")
    row.project_id = project_id
    row.project_by_person = True
    # A decision is upserted by id when the meeting is extracted again and
    # keeps the flag; a model item is deleted and rebuilt unless a correction
    # is on record, so the move is recorded as one.
    if isinstance(row, ExtActionItem):
        service.record_placement(session, row)
    session.flush()


# --- names people say that no project has yet (the user, 2026-10-04) -----------

SUGGEST_MEETINGS = 10
"""How many of the team's latest meetings the suggestions read."""

SUGGEST_MIN_COUNT = 3
"""In how many of those meetings a word must come up -- meetings, not
mentions, so one line repeating a word is one meeting."""

SUGGEST_MAX = 15

_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
_HANGUL = re.compile(r"[가-힣]{2,8}")
_PARTICLES = (
    "에서는",
    "으로는",
    "에서",
    "으로",
    "한테",
    "까지",
    "부터",
    "처럼",
    "보다",
    "이랑",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "의",
    "도",
    "로",
    "과",
    "와",
    "랑",
    "만",
)
_COMMON = frozenset(
    {
        "그거",
        "이거",
        "저거",
        "그건",
        "이건",
        "저희",
        "우리",
        "회의",
        "오늘",
        "내일",
        "어제",
        "다음",
        "이번",
        "지금",
        "일단",
        "그래서",
        "그리고",
        "그러면",
        "그럼",
        "근데",
        "그런데",
        "아니",
        "네네",
        "맞아요",
        "감사합니다",
        "정리",
        "확인",
        "공유",
        "진행",
        "부분",
        "생각",
        "얘기",
        "이야기",
        "문제",
        "내용",
        "관련",
        "정도",
        "하나",
        "다들",
        "이제",
        "혹시",
        "저도",
        "제가",
        "그냥",
        "같이",
        "먼저",
        "나중",
        "금요일",
        "월요일",
        "화요일",
        "수요일",
        "목요일",
        "토요일",
        "일요일",
        "오전",
        "오후",
        "주말",
        "이번주",
        "다음주",
        "시간",
        "일정",
        "자료",
        "문서",
        "작업",
        "담당",
        "기한",
        "결정",
        "할게요",
        "하겠습니다",
        "있어요",
        "없어요",
        "같아요",
    }
)
"""Words that come up in any meeting and name no project. Kept short: a word
wrongly suggested costs a glance, a project name wrongly hidden costs more."""

_LATIN_COMMON = frozenset(
    {
        "the",
        "and",
        "for",
        "you",
        "okay",
        "yes",
        "api",
        "ok",
        "com",
        "net",
        "org",
        "www",
        "http",
        "https",
    }
)

_PREDICATE_ENDINGS = ("요", "죠", "니다", "니까", "세요", "까요", "네요", "어서", "아서")
"""Endings of a verb or an adjective in speech ("맡아주세요", "했습니다"):
such a word is never a project's name."""

_ADDRESSED = ("님", "씨")
"""What follows a person's name when they are spoken to or about -- a word
ending so names a person, whether or not they are on the team."""

_NAME_MARK = re.compile(r"\[사람\d+\][가-힣]*")
"""A scrubbed name with whatever was attached to it (님, a particle)."""

_SURE_PARTICLES = frozenset(p for p in _PARTICLES if len(p) >= 2) | {"은", "는", "을", "를"}
"""Particles no noun ends in; the others ("로", "이", "도"...) end words too --
"마이크로" -- and are taken off only when the word is seen without them."""


def _stem(word: str) -> str:
    """A Korean word without the particle after it ("오튠에서" -> "오튠")."""
    for particle in _PARTICLES:
        if word.endswith(particle) and len(word) - len(particle) >= 2:
            return word[: -len(particle)]
    return word


def suggest_names(
    session: Session, team_id: str, *, now: datetime | None = None
) -> list[tuple[str, int]]:
    """Words that came up in several of the team's latest meetings and that no
    project of the team is named or aliased by -- candidates for a project or
    an alias, with how many meetings each came up in.

    Only the words and their counts leave this function, never a sentence, and
    only consented speakers' lines are read (privacy.md section 5). The team's
    members' names are taken out first, the way an outbound line is scrubbed
    (#411), and so is any word ending in 님 or 씨: a count next to a person's
    name would say how often they were talked about. Only meetings that took
    place count -- not one still scheduled or ahead of ``now``, not one that
    failed.

    Latin words of three letters or more and Korean words of two to eight
    syllables; common words and verb forms left out. A particle that can also
    end a noun is taken off only when the word also comes bare or with another
    particle, so "마이크로" stays whole. A rule, not a model: it misses names and suggests
    some words that are not names, and a person chooses."""
    held = func.coalesce(Meeting.started_at, Meeting.created_at)
    meetings = list(
        session.scalars(
            select(Meeting.id)
            .where(
                Meeting.team_id == team_id,
                service.within_retention(),
                Meeting.status.not_in(("scheduled", "failed")),
                held <= (now or datetime.now(UTC)),
            )
            .order_by(held.desc())
            .limit(SUGGEST_MEETINGS)
        )
    )
    known: set[str] = set()
    for row in team_projects(session, team_id):
        known.add(row.name.lower())
        known.update(a.lower() for a in row.aliases.split("\n") if a)
    roster = list(
        session.scalars(
            select(User.display_name)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
        )
    )

    # Each meeting's raw words, then which Korean stems stand on their own.
    said: list[list[str]] = []
    for meeting_id in meetings:
        consented = service.consented_utterance_ids(session, meeting_id)
        lines = [
            line.text
            for line in service.stored_transcript(session, meeting_id)
            if line.id in consented and line.text
        ]
        words: list[str] = []
        for text in substitute_names(lines, roster):
            text = _NAME_MARK.sub(" ", text)
            words += [w for w in _LATIN.findall(text) if w.lower() not in _LATIN_COMMON]
            words += [
                w
                for w in _HANGUL.findall(text)
                if not _stem(w).endswith(_ADDRESSED) and not w.endswith(_PREDICATE_ENDINGS)
            ]
        said.append(words)
    forms: dict[str, set[str]] = {}
    for words in said:
        for word in words:
            forms.setdefault(_stem(word), set()).add(word)

    def settled(word: str) -> str:
        stem = _stem(word)
        if stem == word or word[len(stem) :] in _SURE_PARTICLES:
            return stem
        seen = forms.get(stem, set())
        return stem if stem in seen or len(seen) >= 2 else word

    counts: dict[str, int] = {}
    shown: dict[str, str] = {}
    for words in said:
        here: dict[str, str] = {}
        for word in map(settled, words):
            key = word.lower()
            if key in _COMMON or key in known or len(word) < 2:
                continue
            here.setdefault(key, word)
        for key, word in here.items():
            counts[key] = counts.get(key, 0) + 1
            shown.setdefault(key, word)
    ranked = sorted(
        ((shown[k], n) for k, n in counts.items() if n >= SUGGEST_MIN_COUNT),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return ranked[:SUGGEST_MAX]
