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

from autune_core import Meeting, Participant, Team, User, Utterance, session_scope
from autune_core.errors import PrivacyViolationError
from autune_gap import service
from autune_gap.config import get_settings
from autune_gap.models import (
    GapGap,
    GapMeetingTemplate,
    GapParticipation,
    GapScoring,
    GapTopic,
    GapTopicEdge,
)
from autune_gap.pipeline import reset_cache
from autune_gap.template import get_template


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


def seed(
    team_id: str,
    topics: dict[str, float],
    *,
    people: int = 2,
    edges: tuple[tuple[str, str, str], ...] = (),
) -> str:
    """A meeting whose topic graph is already built. Returns the meeting id.

    ``topics`` is label -> centrality. Everybody is recorded as having spoken on
    the first topic and silent on the rest, which is a participation matrix with
    both values in it — a matrix that was all one value would let a scoring bug
    that ignores it pass.

    ``edges`` is ``(source label, relation, target label)``, stored one way.
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

        ids: dict[str, str] = {}
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
            ids[label] = topic.id

        for source, relation, target in edges:
            s.add(
                GapTopicEdge(
                    meeting_id=meeting.id,
                    source_topic_id=ids[source],
                    target_topic_id=ids[target],
                    relation=relation,
                    weight=1.0,
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


def test_a_dependency_the_meeting_stated_covers_the_item_without_its_words(
    team_id: str,
) -> None:
    """Neither label says 의존 or 선행, and the edge between them is the meeting
    having said one waits on the other. The edge is read back out of
    ``gap_topic_edges``, which is where step 2 left it."""
    meeting_id = seed(
        team_id,
        {**COVERS_TWO, "정렬 로직": 0.9, "인덱스 재색인": 0.8},
        edges=(("정렬 로직", "depends_on", "인덱스 재색인"),),
    )

    service.detect_gaps(meeting_id)

    assert set(stored(meeting_id)) == {"risk", "next_step"}


def test_two_topics_merely_said_together_do_not_cover_a_dependency(team_id: str) -> None:
    """``co_occurs`` is most of a meeting's edges, and says nothing about how
    the two relate."""
    meeting_id = seed(
        team_id,
        {**COVERS_TWO, "정렬 로직": 0.9, "인덱스 재색인": 0.8},
        edges=(("정렬 로직", "co_occurs", "인덱스 재색인"),),
    )

    service.detect_gaps(meeting_id)

    assert "dependency" in stored(meeting_id)


def test_a_gap_carries_the_template_that_raised_it(team_id: str) -> None:
    """Precision is measured across template edits, and a row that cannot say
    which checklist raised it averages two of them together."""
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)
    gap = stored(meeting_id)["risk"]

    assert gap.template_key == "general"
    assert gap.template_version == "general.5"
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


def test_switching_templates_keeps_what_was_dismissed_under_the_old_one(team_id: str) -> None:
    """A dismissal is threshold tuning's input (ADR 0006), and S20's picker makes
    trying another template one click. Switching away must not throw the
    judgement out, and switching back must find it where it was left."""
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)
    dismissed_id = stored(meeting_id)["risk"].id
    with session_scope() as s:
        s.get(GapGap, dismissed_id).dismissed_at = datetime.now(UTC)

    with session_scope() as s:
        service.set_template(s, meeting_id, "feature_planning")
    service.detect_gaps(meeting_id)

    with session_scope() as s:
        kept = s.get(GapGap, dismissed_id)
        assert kept is not None and kept.template_key == "general"
        assert kept.dismissed_at is not None
        report = service.build_report(s, meeting_id)
    assert dismissed_id not in {gap.id for gap in report.gaps}

    with session_scope() as s:
        service.set_template(s, meeting_id, "general")
    service.detect_gaps(meeting_id)

    back = stored(meeting_id)["risk"]
    assert back.id == dismissed_id
    assert back.dismissed_at is not None


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


# --- a speaker confirmed after scoring (#415) --------------------------------


@pytest.fixture
def rescore_sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """What the rescore hands to ``publish``. E's task is not registered here
    (invariant 2), so the send itself is replaced."""
    captured: list[dict] = []

    def capture(event: str, payload: dict) -> list[str]:
        captured.append(payload)
        return ["autune.intelligence.on_gap_completed"]

    monkeypatch.setattr(service, "publish", capture)
    return captured


@pytest.fixture
def user_id(db_engine: object) -> Iterator[str]:
    with session_scope() as s:
        row = User(email="gap-rescore@example.com", display_name="rescore")
        s.add(row)
        s.flush()
        uid = row.id
    yield uid
    with session_scope() as s:
        s.execute(delete(User).where(User.id == uid))


def split_voice(team_id: str) -> tuple[str, list[str]]:
    """One person diarization split into two labels: on "리스크", the partial
    finding's topic, one label spoke and the other did not. Returns the meeting
    and its participant ids."""
    meeting_id = seed(team_id, {**COVERS_TWO, "리스크": 0.1})
    with session_scope() as s:
        people = list(
            s.scalars(
                select(Participant.id)
                .where(Participant.meeting_id == meeting_id)
                .order_by(Participant.speaker_label)
            )
        )
        risk = s.scalar(
            select(GapTopic.id).where(GapTopic.meeting_id == meeting_id, GapTopic.label == "리스크")
        )
        row = s.scalar(
            select(GapParticipation).where(
                GapParticipation.topic_id == risk, GapParticipation.participant_id == people[0]
            )
        )
        assert row is not None
        row.spoke = True
    return meeting_id, people


def confirm(participant_ids: list[str], user_id: str) -> None:
    """What module A writes when somebody confirms who a speaker is."""
    with session_scope() as s:
        for participant_id in participant_ids:
            participant = s.get(Participant, participant_id)
            assert participant is not None
            participant.user_id = user_id


def test_scoring_records_the_grouping_it_read(team_id: str) -> None:
    meeting_id = seed(team_id, COVERS_TWO)

    service.detect_gaps(meeting_id)

    with session_scope() as s:
        assert s.get(GapScoring, meeting_id) is not None


def test_a_meeting_with_no_topics_records_no_scoring(team_id: str) -> None:
    """So the rescore never publishes a meeting the pipeline has not."""
    meeting_id = seed(team_id, {})

    service.detect_gaps(meeting_id)

    with session_scope() as s:
        assert s.get(GapScoring, meeting_id) is None


def test_a_confirmed_speaker_rescores_the_stored_gap(
    team_id: str, user_id: str, rescore_sent: list[dict]
) -> None:
    """Two labels confirmed as one person: that person spoke on the topic, so
    the stored risk must stop counting half the room as silent on it, and
    match what a fresh detection over the same rows stores."""
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    before = stored(meeting_id)["risk"].risk_score

    confirm(people, user_id)
    rescored = service.rescore_where_people_changed()

    after = stored(meeting_id)["risk"].risk_score
    assert meeting_id in rescored
    assert after < before
    assert meeting_id in {payload["meeting_id"] for payload in rescore_sent}
    service.detect_gaps(meeting_id)
    assert stored(meeting_id)["risk"].risk_score == after


def test_a_rescored_meeting_is_not_rescored_again(
    team_id: str, user_id: str, rescore_sent: list[dict]
) -> None:
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    confirm(people, user_id)
    service.rescore_where_people_changed()

    assert meeting_id not in service.rescore_where_people_changed()


def test_a_meeting_whose_people_did_not_move_is_left_alone(
    team_id: str, rescore_sent: list[dict]
) -> None:
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)

    assert meeting_id not in service.rescore_where_people_changed()
    assert meeting_id not in {payload["meeting_id"] for payload in rescore_sent}


def test_a_rescore_keeps_a_dismissal(team_id: str, user_id: str, rescore_sent: list[dict]) -> None:
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    with session_scope() as s:
        gap = s.get(GapGap, stored(meeting_id)["risk"].id)
        assert gap is not None
        gap.dismissed_at = datetime.now(UTC)

    confirm(people, user_id)
    service.rescore_where_people_changed()

    assert stored(meeting_id)["risk"].dismissed_at is not None


def test_a_privacy_violation_on_rescore_is_raised_after_the_rest(
    team_id: str, user_id: str, rescore_sent: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#506 review: the verifier raises ``PrivacyViolationError`` on purpose, and
    the rescore's catch-all turned it into a warning every ten minutes. The
    other meetings are still rescored; then the sweep fails with ids only."""
    first, first_people = split_voice(team_id)
    second, second_people = split_voice(team_id)
    service.detect_gaps(first)
    service.detect_gaps(second)
    confirm(first_people + second_people, user_id)
    # The sweep goes in id order, so the first call is the leaking meeting.
    leaking, fine = sorted([first, second])

    real_hear = service._hear
    calls: list[int] = []

    def hear(chosen, speech, settings):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 1:
            raise PrivacyViolationError("unmasked 010-1234-5678 in a verifier request")
        return real_hear(chosen, speech, settings)

    monkeypatch.setattr(service, "_hear", hear)

    with pytest.raises(PrivacyViolationError) as raised:
        service.rescore_where_people_changed()

    assert leaking in str(raised.value)
    # Not a bare "010": the message carries random hex ids that can contain it (#662).
    assert "1234-5678" not in str(raised.value)
    assert {payload["meeting_id"] for payload in rescore_sent} == {fine}


@pytest.fixture
def max_attempts() -> Iterator[int]:
    settings = get_settings()
    original = settings.rescore_max_attempts
    settings.rescore_max_attempts = 2
    try:
        yield 2
    finally:
        settings.rescore_max_attempts = original


REAL_HEAR = service._hear


def failing_hear(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make every detection fail the way a provider outage would, and count the
    tries. Not a privacy violation: those are raised, not counted."""
    calls: list[int] = []

    def hear(chosen, speech, settings):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(service, "_hear", hear)
    return calls


def test_a_meeting_that_keeps_failing_is_left_alone_after_the_cap(
    team_id: str,
    user_id: str,
    rescore_sent: list[dict],
    max_attempts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#516: without a cap the sweep retried it every ten minutes forever,
    spending a hosted verifier's quota each time."""
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    confirm(people, user_id)
    calls = failing_hear(monkeypatch)

    for _ in range(max_attempts + 2):
        assert meeting_id not in service.rescore_where_people_changed()

    assert len(calls) == max_attempts
    with session_scope() as s:
        row = s.get(GapScoring, meeting_id)
        assert row is not None
        assert row.rescore_failures == max_attempts
        assert row.last_failed_at is not None
    assert rescore_sent == []


def test_a_meeting_given_up_on_is_tried_again_when_its_people_move(
    team_id: str,
    user_id: str,
    rescore_sent: list[dict],
    max_attempts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap belongs to one grouping. A later change of people is a new
    question, and a fixed provider should get to answer it. The change here is
    a withdrawn consent: with two participants, splitting them apart again
    would only restore the grouping the gaps were scored at, which is no
    change at all."""
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    confirm(people, user_id)
    calls = failing_hear(monkeypatch)
    for _ in range(max_attempts):
        service.rescore_where_people_changed()
    assert len(calls) == max_attempts

    monkeypatch.setattr(service, "_hear", REAL_HEAR)
    with session_scope() as s:
        withdrawn = s.get(Participant, people[1])
        assert withdrawn is not None
        withdrawn.consented = False

    assert meeting_id in service.rescore_where_people_changed()


def test_a_rescore_that_succeeds_forgets_the_failures(
    team_id: str,
    user_id: str,
    rescore_sent: list[dict],
    max_attempts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    confirm(people, user_id)
    failing_hear(monkeypatch)
    service.rescore_where_people_changed()

    monkeypatch.setattr(service, "_hear", REAL_HEAR)
    assert meeting_id in service.rescore_where_people_changed()

    with session_scope() as s:
        row = s.get(GapScoring, meeting_id)
        assert row is not None
        assert (row.failed_people_key, row.rescore_failures, row.last_failed_at) == (None, 0, None)


def test_a_rerun_that_leaves_no_topics_forgets_the_scoring(
    team_id: str, user_id: str, rescore_sent: list[dict]
) -> None:
    """#506 review: a row left at the old grouping would disagree forever, and
    rerun and republish the meeting every ten minutes."""
    meeting_id, people = split_voice(team_id)
    service.detect_gaps(meeting_id)
    with session_scope() as s:
        s.execute(delete(GapTopic).where(GapTopic.meeting_id == meeting_id))

    service.detect_gaps(meeting_id)
    confirm(people, user_id)

    with session_scope() as s:
        assert s.get(GapScoring, meeting_id) is None
    assert meeting_id not in service.rescore_where_people_changed()


# --- speech read by meaning ---------------------------------------------------


@pytest.fixture
def fake_embedder() -> Iterator[None]:
    """``AUTUNE_GAP_EMBEDDER_IMPL=fake`` for one test. The fake is lexical, so
    an utterance written as one of an item's example sentences is nearest to
    that item — which is what these tests need, and says nothing about KURE-v1."""
    settings = get_settings()
    original = settings.embedder_impl
    settings.embedder_impl = "fake"
    reset_cache()
    try:
        yield
    finally:
        settings.embedder_impl = original
        reset_cache()


def say(meeting_id: str, *lines: str, consented: bool = True) -> None:
    with session_scope() as s:
        person = Participant(meeting_id=meeting_id, speaker_label="발화자", consented=consented)
        s.add(person)
        s.flush()
        for index, text in enumerate(lines):
            s.add(
                Utterance(
                    id=f"utt_{person.id}_{index}",
                    meeting_id=meeting_id,
                    participant_id=person.id,
                    speaker_label="발화자",
                    start_sec=float(index),
                    end_sec=float(index) + 1,
                    text=text,
                )
            )


def dependency_example() -> str:
    general = get_template("general")
    return next(item for item in general.items if item.key == "dependency").examples[0]


def test_an_item_the_embedder_heard_is_stored_partial(team_id: str, fake_embedder: None) -> None:
    """The ``no-noun`` fix end to end: no keyword of ``dependency`` was said and
    no topic names it, and the item is raised as partial rather than missing."""
    meeting_id = seed(team_id, COVERS_TWO)
    say(meeting_id, dependency_example())

    service.detect_gaps(meeting_id)

    assert stored(meeting_id)["dependency"].coverage == "partial"
    assert stored(meeting_id)["risk"].coverage == "missing"


def test_with_the_embedder_off_the_same_meeting_is_missing(team_id: str) -> None:
    """The baseline, kept: nothing heard by meaning, keywords only."""
    meeting_id = seed(team_id, COVERS_TWO)
    say(meeting_id, dependency_example())

    service.detect_gaps(meeting_id)

    assert stored(meeting_id)["dependency"].coverage == "missing"


def test_the_embedder_reads_only_consenting_speech(team_id: str, fake_embedder: None) -> None:
    """privacy.md section 5: a participant who declined is not analysed, by the
    keyword reading or by this one."""
    meeting_id = seed(team_id, COVERS_TWO)
    say(meeting_id, dependency_example(), consented=False)

    service.detect_gaps(meeting_id)

    assert stored(meeting_id)["dependency"].coverage == "missing"


# --- sending E the report again after S20 changed it (#316, #471) -----------


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """What ``republish_report`` hands to ``publish``. E's task is not
    registered here (invariant 2), so the send itself is replaced."""
    captured: list[dict] = []

    def capture(event: str, payload: dict) -> list[str]:
        captured.append(payload)
        return ["autune.intelligence.on_gap_completed"]

    monkeypatch.setattr(service, "publish", capture)
    return captured


def test_a_dismissed_gap_leaves_the_report_e_is_sent_again(team_id: str, sent: list[dict]) -> None:
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)
    dismissed = stored(meeting_id)["risk"].id
    with session_scope() as s:
        gap = s.get(GapGap, dismissed)
        assert gap is not None
        gap.dismissed_at = datetime.now(UTC)

    report = service.republish_report(meeting_id)

    assert report is not None
    assert len(sent) == 1
    assert dismissed not in {gap["id"] for gap in sent[0]["gaps"]}
    assert len(sent[0]["gaps"]) == 2


def test_a_template_switch_reaches_e_as_the_new_checklist(team_id: str, sent: list[dict]) -> None:
    meeting_id = seed(team_id, COVERS_TWO)
    service.detect_gaps(meeting_id)
    with session_scope() as s:
        service.set_template(s, meeting_id, "feature_planning")
    service.detect_gaps(meeting_id)

    service.republish_report(meeting_id)

    items = {item.item for item in get_template("feature_planning").items}
    assert sent[0]["gaps"]
    assert {gap["template_item"] for gap in sent[0]["gaps"]} <= items


def test_a_meeting_the_pipeline_has_not_analysed_is_not_published(
    team_id: str, sent: list[dict]
) -> None:
    """A first publish would start E's countdown for a meeting B and D have
    not reached."""
    meeting_id = seed(team_id, {})

    assert service.republish_report(meeting_id) is None
    assert sent == []
