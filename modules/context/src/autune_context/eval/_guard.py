"""Refuse to run the evaluation against a database that holds real meetings.

The runners seed and delete shared entities -- ``Team``, ``Meeting`` and, for
topic linking, ``Utterance`` rows -- the one place module D writes them
(invariant 4 otherwise forbids it). That is only acceptable in a throwaway
database. Each case deletes its own ``eval-<case id>`` team, but a run killed
mid-case leaves one behind, so those are ignored here rather than blocking the
next run.

The check is on contents, not the database's name: a name says nothing about
whether real meetings are in it.
"""

from __future__ import annotations

from sqlalchemy import func, select

from autune_core import Meeting, Team, session_scope

EVAL_TEAM_PREFIX = "eval-"


class RealMeetingsPresentError(RuntimeError):
    pass


def real_meeting_count() -> int:
    """Meetings in this database that no evaluation run created."""
    with session_scope() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(Meeting)
                .join(Team, Team.id == Meeting.team_id)
                .where(~Team.name.startswith(EVAL_TEAM_PREFIX, autoescape=True))
            )
            or 0
        )


def refuse_real_meetings() -> None:
    real = real_meeting_count()
    if real:
        raise RealMeetingsPresentError(
            f"this database holds {real} meeting(s) that the evaluation did not "
            "create; it seeds and deletes teams, meetings and utterances, so run it "
            "against a throwaway database instead: createdb <name>, point "
            "AUTUNE_DATABASE_URL at it, then `uv run alembic -c infra/alembic.ini "
            "upgrade heads`"
        )
