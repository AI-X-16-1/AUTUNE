"""The top line of an item or a decision in a copy that leaves Autune.

Module B's owner, 2026-10-09: a copy leads with the row's short title where
it has one ("Slack·회의록까지 전부") and says which kind it is ("밖으로 나가는
제목 전부") -- outside the board there are no tabs to tell a decision from
something to do.

**The title** is ``ExtActionItem.title`` / ``ExtDecision.title``: twenty
characters or fewer, written by ``pipeline.title`` from the stored sentence
and made of that sentence's own words, so it carries nothing the sentence
does not. Most rows have none -- a sentence a person typed or edited, a
decision a person reworded, one whose title was refused, every row from
before -- and such a row leads with its sentence, as every copy did until
now. Where the copy has a body (a Jira description, a Notion property, a
calendar event's description) the whole sentence goes there when the title
took its place; a line in a message has no body, and is the title alone.

**The kind mark** is ``ITEM_MARK`` or ``DECISION_MARK``, put before the top
line by the code that builds a copy. It is not part of the stored title and
does not count against its twenty characters. ``MARKED`` says which copies
carry it: the ones with a title of their own. A line in a message or in
minutes already sits under a heading that says the kind ("할 일", "결정",
"확정:"), and takes none.

Nothing here reads a row or sends anything: strings in, a string out. Every
copy still leaves through its integration client, whose ``check_outbound``
reads the top line as it reads the sentence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

Kind = Literal["item", "decision"]

Copy = Literal["jira", "notion", "calendar", "reminder", "digest", "notice", "report", "minutes"]
"""A copy B sends: a Jira issue, a Notion page, a calendar event, the line of
a due-date reminder, a line of the weekly or morning digest, of the
after-meeting notice, of the work report, of a project's minutes."""

ITEM_MARK = "[할 일] "
DECISION_MARK = "[결정] "
"""What marks a decision among a team's tasks. A decision's Jira issue has
carried it since 2026-10-04 (``jira_sync.DECISION_PREFIX`` is this)."""

MARKS: Mapping[Kind, str] = {"item": ITEM_MARK, "decision": DECISION_MARK}

MARKED: Mapping[Copy, bool] = {
    "jira": True,
    "notion": True,
    "calendar": True,
    "reminder": False,
    "digest": False,
    "notice": False,
    "report": False,
    "minutes": False,
}
"""Whether a copy's top line carries the kind mark. One switch a copy."""


def titled(title: str | None, sentence: str) -> bool:
    """Whether the row's title stands in place of its sentence: it has one, and
    it is not the sentence itself."""
    written = (title or "").strip()
    return bool(written) and written != sentence.strip()


def top_line(title: str | None, sentence: str) -> str:
    """The row's short title when it has one, else its sentence."""
    return (title or "").strip() or sentence


def outbound_line(copy: Copy, kind: Kind, title: str | None, sentence: str) -> str:
    """The top line as ``copy`` carries it: the kind mark where that copy takes
    one, then the title or the sentence."""
    return (MARKS[kind] if MARKED[copy] else "") + top_line(title, sentence)


def standing_title(title: str | None, *, reworded: bool) -> str | None:
    """A decision's title, or none once a person reworded the decision: the
    title is of the model's sentence, and what stands is the person's."""
    return None if reworded else title
