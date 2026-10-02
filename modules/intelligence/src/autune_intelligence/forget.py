"""A person deleted their own speech: E lets go of the words it copied (#587, #614).

E keeps text drawn from utterances in three places: its copies of B's, C's and
D's results (``intel_completion``), the meeting reports quoting them
(``intel_meeting_reports``), and nothing else -- the weekly report holds counts
and pattern names only. B, C and D each forget their own rows on the same
signal; their copies in E are E's to forget (#614), and C does not republish a
meeting it left with no topic at all.

E cannot read B's or C's tables, so it applies their rules to its copies with
what it can see -- the utterances themselves, which still exist when the hook
runs:

- **B (``ExtractionResult``).** The work stays, the words go. An action item
  still awaiting confirmation that was drawn from a deleted utterance is
  dropped, as B deletes such a draft whatever it says. Any other item, and a
  decision, keeps its entry; its text reads ``SPEECH_DELETED_TEXT`` when it *is*
  one of its own deleted lines -- equal to it, the line with B's
  " (담당 ..., 기한 ...)" tail, or, both long enough, one containing the other
  -- and a decision's also when every line it came from is deleted (B replaces
  a model decision with no cited lines). A summary or a person's writing
  stays. A per-utterance classification or ambiguous agreement on a deleted
  utterance is dropped.
- **C (``GapReport``).** A topic goes only when every utterance it was built
  from goes, and its participation with it. A gap stays; a question naming a
  topic that went is cleared (C falls back to the template's general question,
  which E does not know -- no question rather than a guess).
- **D (``ContextLinks``).** A decision change follows its decision: a statement
  E replaced reads the same here, in this meeting's lineage and as the
  ``previous_statement`` of later meetings of the team. A topic link to a topic
  that went is dropped.
- **Reports.** Every text replaced above, and every deleted line itself, is
  replaced wherever a report or a correction quotes it; the rest of the report
  stays. A copy already posted to Slack is outside Autune and is not recalled.

Safe to repeat: the second time the texts are already replaced. Ids and counts
only leave this module.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Utterance

from .models import IntelCompletion, IntelMeetingReport

SPEECH_DELETED_TEXT: Final = "삭제된 발화에서 만든 항목"
"""What replaces a text that was the deleted speech itself -- B's words (#587)."""

_MIN_QUOTE: Final = 10
"""Shorter texts are compared whole, never by containment: "네", "좋아요" would
match half the meeting."""


@dataclass(frozen=True)
class SpeechForgotten:
    """What E changed, by id and count."""

    meetings: tuple[str, ...] = ()
    texts_replaced: int = 0
    topics_removed: int = 0
    reports_changed: int = 0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_line(text: str, lines: Collection[str]) -> bool:
    """Whether ``text`` is one of ``lines`` -- the deleted lines its own entry was
    drawn from, so a short line cannot match some other entry's text.

    Equal to it; the line with the " (담당 ..., 기한 ...)" tail B adds to a
    decision it lifted verbatim (#725 review); or, both long enough to mean
    something, one containing the other.
    """
    t = _norm(text)
    if not t:
        return False
    for line in lines:
        if t == line or t.startswith(line + " ("):
            return True
        if len(t) >= _MIN_QUOTE and len(line) >= _MIN_QUOTE and (t in line or line in t):
            return True
    return False


def forget_speech(session: Session, utterance_ids: Collection[str]) -> SpeechForgotten:
    gone = set(utterance_ids)
    if not gone:
        return SpeechForgotten()
    rows = session.execute(
        sa.select(Utterance.id, Utterance.meeting_id, Utterance.text).where(Utterance.id.in_(gone))
    ).all()
    meetings = sorted({meeting_id for _, meeting_id, _ in rows})
    line_of = {utterance_id: _norm(text) for utterance_id, _, text in rows if _norm(text)}
    lines = set(line_of.values())
    if not meetings:
        return SpeechForgotten()

    replaced: set[str] = set()  # the originals, to find them in reports
    removed_labels: set[str] = set()
    redacted_decisions: dict[str, str] = {}  # decision id -> its original statement
    texts = topics = 0

    for completion in session.scalars(
        sa.select(IntelCompletion).where(IntelCompletion.meeting_id.in_(meetings)).with_for_update()
    ):
        if completion.extraction_payload is not None:
            payload, n = _forget_extraction(
                completion.extraction_payload, gone, line_of, replaced, redacted_decisions
            )
            completion.extraction_payload, texts = payload, texts + n
        if completion.gap_payload is not None:
            payload, n_topics, n_questions = _forget_gap(
                completion.gap_payload, gone, replaced, removed_labels
            )
            completion.gap_payload = payload
            topics, texts = topics + n_topics, texts + n_questions
        if completion.context_payload is not None:
            payload, n = _forget_context(
                completion.context_payload, redacted_decisions, removed_labels, replaced
            )
            completion.context_payload, texts = payload, texts + n

    texts += _forget_later_lineage(session, meetings, redacted_decisions, replaced)
    quoted = replaced | {_norm(t) for t in replaced} | lines
    reports = _forget_in_reports(session, meetings, quoted)
    session.flush()
    return SpeechForgotten(
        meetings=tuple(meetings),
        texts_replaced=texts,
        topics_removed=topics,
        reports_changed=reports,
    )


def _forget_extraction(
    payload: dict[str, Any],
    gone: set[str],
    line_of: dict[str, str],
    replaced: set[str],
    redacted_decisions: dict[str, str],
) -> tuple[dict[str, Any], int]:
    out = dict(payload)
    changed = 0
    items = []
    for item in payload.get("action_items", []):
        item = dict(item)
        own = gone & set(item.get("source_utterance_ids", []))
        if not own:
            items.append(item)
            continue
        if item.get("status", "needs_confirmation") == "needs_confirmation":
            # Nobody accepted it: B deletes the draft, whatever it says (#725 review).
            replaced.add(item.get("description", ""))
            changed += 1
            continue
        if _is_line(item.get("description", ""), [line_of[u] for u in own if u in line_of]):
            replaced.add(item["description"])
            item["description"] = SPEECH_DELETED_TEXT
            changed += 1
        items.append(item)
    out["action_items"] = items
    decisions = []
    for decision in payload.get("decisions", []):
        decision = dict(decision)
        sources = set(decision.get("source_utterance_ids", []))
        own = gone & sources
        if (
            own
            and decision.get("statement") != SPEECH_DELETED_TEXT
            and (
                own == sources
                or _is_line(
                    decision.get("statement", ""), [line_of[u] for u in own if u in line_of]
                )
            )
        ):
            replaced.add(decision["statement"])
            redacted_decisions[decision["id"]] = decision["statement"]
            decision["statement"] = SPEECH_DELETED_TEXT
            changed += 1
        decisions.append(decision)
    out["decisions"] = decisions
    for key in ("classifications", "ambiguous_agreements"):
        if key in payload:
            kept = [entry for entry in payload[key] if entry.get("utterance_id") not in gone]
            changed += len(payload[key]) - len(kept)
            out[key] = kept
    return out, changed


def _forget_gap(
    payload: dict[str, Any], gone: set[str], replaced: set[str], removed_labels: set[str]
) -> tuple[dict[str, Any], int, int]:
    out = dict(payload)
    removed: dict[str, str] = {}  # topic id -> label
    topics = []
    for topic in payload.get("topics", []):
        said = set(topic.get("utterance_ids", []))
        if said and said <= gone:
            removed[topic["id"]] = topic.get("label", "")
            continue
        topics.append({**topic, "utterance_ids": sorted(said - gone)})
    if not removed:
        return payload, 0, 0
    out["topics"] = topics
    out["participation"] = [
        p for p in payload.get("participation", []) if p.get("topic_id") not in removed
    ]
    labels = {label for label in removed.values() if label}
    removed_labels |= labels
    questions = 0
    gaps = []
    for gap in payload.get("gaps", []):
        gap = dict(gap)
        gap["related_topic_ids"] = [t for t in gap.get("related_topic_ids", []) if t not in removed]
        question = gap.get("suggested_question")
        if question and any(label in question for label in labels):
            replaced.add(question)
            gap["suggested_question"] = None
            questions += 1
        gaps.append(gap)
    out["gaps"] = gaps
    return out, len(removed), questions


def _forget_context(
    payload: dict[str, Any],
    redacted_decisions: dict[str, str],
    removed_labels: set[str],
    replaced: set[str],
) -> tuple[dict[str, Any], int]:
    out = dict(payload)
    changed = 0
    lineage = []
    for change in payload.get("decision_lineage", []):
        change = dict(change)
        if (
            change.get("source_decision_id") in redacted_decisions
            and change.get("current_statement") != SPEECH_DELETED_TEXT
        ):
            replaced.add(change.get("current_statement") or "")
            change["current_statement"] = SPEECH_DELETED_TEXT
            changed += 1
        lineage.append(change)
    out["decision_lineage"] = lineage
    links = [
        link
        for link in payload.get("topic_links", [])
        if link.get("topic_label") not in removed_labels
    ]
    changed += len(payload.get("topic_links", [])) - len(links)
    out["topic_links"] = links
    return out, changed


def _forget_later_lineage(
    session: Session,
    meetings: Iterable[str],
    redacted_decisions: dict[str, str],
    replaced: set[str],
) -> int:
    """Later meetings of the team quote a replaced decision as their
    ``previous_statement``; it reads the same there."""
    statements = set(redacted_decisions.values())
    if not statements:
        return 0
    affected = set(meetings)
    teams = set(session.scalars(sa.select(Meeting.team_id).where(Meeting.id.in_(affected))))
    changed = 0
    for completion in session.scalars(
        sa.select(IntelCompletion)
        .join(Meeting, Meeting.id == IntelCompletion.meeting_id)
        .where(Meeting.team_id.in_(teams), IntelCompletion.context_payload.is_not(None))
        .with_for_update(of=IntelCompletion)
    ):
        payload = completion.context_payload or {}
        lineage = []
        touched = False
        for change in payload.get("decision_lineage", []):
            change = dict(change)
            if (
                change.get("previous_meeting_id") in affected
                and change.get("previous_statement") in statements
            ):
                replaced.add(change["previous_statement"])
                change["previous_statement"] = SPEECH_DELETED_TEXT
                changed += 1
                touched = True
            lineage.append(change)
        if touched:
            completion.context_payload = {**payload, "decision_lineage": lineage}
    return changed


def _forget_in_reports(session: Session, meetings: Iterable[str], quoted: set[str]) -> int:
    """Replace each forgotten text where a report or a correction quotes it.
    A report holds its own meeting's content only (#459), so only these
    meetings' reports are read."""
    needles = sorted((t for t in quoted if t and t != SPEECH_DELETED_TEXT), key=len, reverse=True)
    if not needles:
        return 0
    changed = 0
    for row in session.scalars(
        sa.select(IntelMeetingReport)
        .where(IntelMeetingReport.meeting_id.in_(list(meetings)))
        .with_for_update()
    ):
        body = _replace_quotes(row.body_markdown, needles)
        correction = _replace_quotes(row.correction_body, needles) if row.correction_body else None
        if body != row.body_markdown or correction != row.correction_body:
            row.body_markdown = body
            row.correction_body = correction
            changed += 1
    return changed


def _replace_quotes(text: str, needles: list[str]) -> str:
    for needle in needles:
        if len(needle) < _MIN_QUOTE and needle != _norm(text):
            # A short text is replaced only where it stands as a line's own text:
            # the whole line, or a bullet's head before " — owner · date".
            pattern = re.compile(rf"(?m)^(\s*(?:[-•*]\s*)?){re.escape(needle)}(?=\s*(?:—|$))")
            text = pattern.sub(lambda m: m.group(1) + SPEECH_DELETED_TEXT, text)
            continue
        text = text.replace(needle, SPEECH_DELETED_TEXT)
    return text
