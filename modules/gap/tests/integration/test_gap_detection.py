"""Template comparison against a real PostgreSQL: what gets stored, and what a
re-run does to it.

Seeds ``gap_topics`` and ``gap_participation`` directly rather than running a
transcript through extraction. Detection reads stored rows — that is the point
of it being a separate step — so a test that went through the extractor would be
testing the extractor, and the topic labels here have to be exact for the
template keywords to match.

``service`` opens its own sessions through ``session_scope``, so these commit
real rows and clean up by deleting the team.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from autune_core import Meeting, Participant, Team, session_scope
from autune_gap import service
from autune_gap.config import get_settings
from autune_gap.models import GapGap, GapMeetingTemplate, GapParticipation, GapTopic


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="gap-detect-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def seed(team_id: str, topics: dict[str, float], *, people: int = 2) -> str:
    """A meeting whose topic graph is already built. Returns the meeting id.

    ``topics`` is label -> centrality. Everybody is recorded as having spoken on
    the first topic and silent on the rest, which is a participation matrix with
    both values in it — a matrix that was all one value would let a scoring bug
    that ignores it pass.
    """
    with session_scope() as s:
        meeting = Meeting(team_id=team_id, title="회의", status="analyzing")
        s.add(meeting)
        s.flush()

        participants = []
        for index in range(people):
            person = Participant(
                meeting_id=meeting.id, speaker_label=f"화자{index}", consented=True
            )
            s.add(person)
            s.flush()
            participants.append(person.id)

        for position, (label, centrality) in enumerate(topics.items()):
            topic = GapTopic(
                meeting_id=meeting.id,
                label=label,
                extractor_version="fake",
                centrality=centrality,
                betweenness=0.0,
            )
            s.add(topic)
            s.flush()
            for participant_id in participants:
                s.add(
                    GapParticipation(
                        topic_id=topic.id, participant_id=participant_id, spoke=position == 0
                    )
                )

        return meeting.id


COVERS_TWO = {"핵심 지표": 1.0, "담당자": 0.9}
"""Matches ``general``'s ``success_criteria`` and ``ownership`` and nothing
else, so the meeting is left short of ``risk``, ``dependency`` and
``next_step``."""


def stored(meeting_id: str) -> dict[str, GapGap]:
    with session_scope() as s:
        return {
            row.template_item_key: row
            for row in s.scalars(select(GapGap).where(GapGap.meeting_id == meeting_id))
        }


# --- what a pass stores -----------------------------------------------------


def test_the_items_a_meeting_missed_become_gaps(team_id: str) -> None:
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)

    assert set(stored(meeting_id)) == {"risk", "dependency", "next_step"}


def test_a_gap_carries_the_template_that_raised_it(team_id: str) -> None:
    """Precision is measured across template edits, and a row that cannot say
    which checklist raised it averages two of them together."""
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)
    gap = stored(meeting_id)["risk"]

    assert gap.template_key == "general"
    assert gap.template_version == "general.1"
    assert gap.template_item == "리스크·예외 처리"
    assert gap.suggested_question


def test_a_gap_stores_the_coverage_it_was_classified_as(team_id: str) -> None:
    """The S20 rail reads this column rather than re-running ``classify``, so
    "named it and moved on" and "never came up" have to survive the write.

    ``covered`` is never stored: a covered item raises no gap, so the rail reads
    the absence of a row (``service.template_comparison``).
    """
    meeting_id = seed(team_id, {**COVERS_TWO, "리스크": 0.1})

    service.detect_gaps(meeting_id)
    rows = stored(meeting_id)

    assert rows["risk"].coverage == "partial"
    assert rows["dependency"].coverage == "missing"


def test_the_rail_reports_an_item_no_gap_was_raised_for_as_covered(team_id: str) -> None:
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)
    with session_scope() as session:
        comparison = service.template_comparison(session, meeting_id)

    states = {entry.key: entry.coverage for entry in comparison.items}

    assert comparison.analysed is True
    assert states["success_criteria"] == "covered"
    assert states["dependency"] == "missing"


def test_a_gap_points_at_no_topic_when_the_meeting_never_raised_one(team_id: str) -> None:
    """A missing item was inferred from the absence of a topic, so there is
    nothing for ``gap_related_topics`` to point at."""
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)
    report = service.publish_report(meeting_id)

    assert [gap.related_topic_ids for gap in report.gaps] == [[], [], []]


def test_a_partial_gap_points_at_the_topic_it_was_inferred_from(team_id: str) -> None:
    """ "리스크" named at the edge of the graph is a partial finding, and the
    report has to be able to show which topic that was."""
    meeting_id = seed(team_id, {**COVERS_TWO, "리스크": 0.1})

    service.detect_gaps(meeting_id)
    report = service.publish_report(meeting_id)

    risky = next(gap for gap in report.gaps if gap.template_item == "리스크·예외 처리")
    assert len(risky.related_topic_ids) == 1


def test_a_meeting_with_no_topics_gets_no_gaps(team_id: str) -> None:
    """An empty graph is extraction having found nothing, not the meeting
    having discussed nothing — see ``detect.compare``."""
    meeting_id = seed(team_id, {})

    service.detect_gaps(meeting_id)

    assert stored(meeting_id) == {}


def test_the_report_carries_what_detection_stored(team_id: str) -> None:
    """E receives ``GapReport``, and until this step existed its ``gaps`` list
    was empty in practice rather than by construction."""
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)
    report = service.publish_report(meeting_id)

    assert {gap.title for gap in report.gaps}
    assert [gap.risk_score for gap in report.gaps] == sorted(
        (gap.risk_score for gap in report.gaps), reverse=True
    )


# --- what a re-run does -----------------------------------------------------


def test_a_re_run_keeps_the_identity_of_a_gap_it_already_raised(team_id: str) -> None:
    """A link somebody sent to a gap has to still open it."""
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)
    first = {key: gap.id for key, gap in stored(meeting_id).items()}

    service.detect_gaps(meeting_id)

    assert {key: gap.id for key, gap in stored(meeting_id).items()} == first


def test_a_re_run_keeps_a_dismissal(team_id: str) -> None:
    """Somebody called this gap a false positive. That judgement is the input
    ADR 0006's threshold tuning reads, and re-analysing the meeting must not
    quietly throw it away."""
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)

    with session_scope() as s:
        gap = s.get(GapGap, stored(meeting_id)["risk"].id)
        assert gap is not None
        gap.dismissed_at = datetime.now(UTC)

    service.detect_gaps(meeting_id)

    assert stored(meeting_id)["risk"].dismissed_at is not None


def test_a_gap_the_meeting_now_covers_is_removed(team_id: str) -> None:
    """A gap that is no longer a gap should not sit in the table waiting to be
    counted."""
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)
    assert "risk" in stored(meeting_id)

    with session_scope() as s:
        s.add(
            GapTopic(
                meeting_id=meeting_id,
                label="리스크",
                extractor_version="fake",
                centrality=0.9,
                betweenness=0.0,
            )
        )

    service.detect_gaps(meeting_id)

    assert "risk" not in stored(meeting_id)


# --- which template applies -------------------------------------------------


def test_a_meeting_nobody_chose_for_gets_the_default(team_id: str) -> None:
    meeting_id = seed(team_id, COVERS_TWO)

    with session_scope() as s:
        assert service.selected_template_key(s, meeting_id) == get_settings().default_template


def test_an_override_changes_which_checklist_the_meeting_is_held_to(team_id: str) -> None:
    """``feature_planning`` adds five items, so the same meeting is short of
    more of them."""
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)
    assert len(stored(meeting_id)) == 3

    with session_scope() as s:
        service.set_template(s, meeting_id, "feature_planning")
    service.detect_gaps(meeting_id)

    after = stored(meeting_id)
    assert len(after) == 8
    assert {gap.template_key for gap in after.values()} == {"feature_planning"}


def test_switching_templates_drops_the_rows_the_old_one_raised(team_id: str) -> None:
    """The gaps of a checklist the meeting is no longer held to are not gaps."""
    meeting_id = seed(team_id, COVERS_TWO)
    with session_scope() as s:
        service.set_template(s, meeting_id, "feature_planning")
    service.detect_gaps(meeting_id)

    with session_scope() as s:
        service.set_template(s, meeting_id, "general")
    service.detect_gaps(meeting_id)

    assert {gap.template_key for gap in stored(meeting_id).values()} == {"general"}


def test_an_override_naming_a_template_that_no_longer_exists_falls_back(team_id: str) -> None:
    """A deleted template file is the deployment's problem, and refusing to
    analyse the meeting does not make it less so."""
    meeting_id = seed(team_id, COVERS_TWO)
    with session_scope() as s:
        s.add(GapMeetingTemplate(meeting_id=meeting_id, template_key="retrospective"))

    with session_scope() as s:
        assert service.selected_template_key(s, meeting_id) == get_settings().default_template


def test_the_override_goes_when_the_meeting_does(team_id: str) -> None:
    """Everything module C owns reaches deletion through ``meetings.id``, which
    is why there is no deletion hook."""
    meeting_id = seed(team_id, COVERS_TWO)
    with session_scope() as s:
        service.set_template(s, meeting_id, "feature_planning")

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == meeting_id))

    with session_scope() as s:
        assert s.get(GapMeetingTemplate, meeting_id) is None
