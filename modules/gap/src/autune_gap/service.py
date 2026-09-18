"""Business logic for module C: Gap Detection.

Owner: 박재경. See docs/modules/gap.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``gap_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, nulls_last, select

from autune_contracts.enums import GapSeverity
from autune_contracts.events import GAP_COMPLETED
from autune_contracts.gap import Gap, GapReport, Participation, Topic
from autune_core import (
    Meeting,
    Participant,
    TeamMember,
    User,
    Utterance,
    get_logger,
    ids,
    new_id,
    session_scope,
)
from autune_core.errors import NotFoundError
from autune_core.events import publish
from autune_gap import detect, graph, template
from autune_gap.config import GapSettings, get_settings
from autune_gap.models import (
    GapGap,
    GapMeetingTemplate,
    GapParticipation,
    GapRelatedTopic,
    GapTopic,
    GapTopicEdge,
    GapTopicUtterance,
)
from autune_gap.pipeline import get_entity_extractor
from autune_gap.schemas import TemplateRead, TopicEdgeRead, TopicGraphRead, TopicNodeRead

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from autune_contracts import TranscriptReady

log = get_logger(__name__)


def require_readable_meeting(session: Session, meeting_id: str, reader: User) -> None:
    """Raise unless ``reader`` may read this meeting. Everything under
    ``/api/gap`` that names a meeting calls this first.

    A token proves who is asking, not whose meetings they may read.

    **An unknown meeting and somebody else's meeting get the same answer.** Both
    are ``NotFoundError``, never a 403 — a 403 confirms that the id exists, and
    the ids are the only thing a caller needs to walk the table. Module A checks
    existence before membership and therefore tells a non-member which meeting
    ids are real (``modules/audio/.../service.py:64``); #276 asks for that to
    change there too, and C does not copy it in the meantime.

    Not a 404 for a meeting that exists, is readable, and has not been analysed:
    the resource is there and has produced nothing, which is what an empty
    report says. A screen polling while the pipeline runs needs that difference,
    and module B draws the same line on ``/results/{meeting_id}``.

    The refusal is logged with the reason, because "no such meeting" and "not
    your team" are the same answer to a caller and different answers to whoever
    is reading the logs. Ids only — a meeting title is meeting content.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        log.info(
            "gap_read_refused", meeting_id=meeting_id, reader_id=reader.id, reason="no_such_meeting"
        )
        raise NotFoundError("meeting", meeting_id)

    if not _is_team_member(session, user_id=reader.id, team_id=meeting.team_id):
        log.info(
            "gap_read_refused", meeting_id=meeting_id, reader_id=reader.id, reason="not_a_member"
        )
        raise NotFoundError("meeting", meeting_id)


def _is_team_member(session: Session, *, user_id: str, team_id: str) -> bool:
    """Whether this user belongs to this team.

    A predicate rather than a raising helper, because the caller above answers
    both of its cases the same way and a helper that raised its own error would
    have to be caught and translated.

    **This duplicates four lines of module A** (``require_team_member``), and
    that is what #276 is deciding: the same check living in two modules is one
    that can come to mean two things — A's own docstring says so about two
    copies inside one module. If it moves to ``packages/core`` where
    ``require_self`` already lives, this function becomes a call to it and the
    behaviour above does not change. Until that decision, invariant 2 forbids
    importing A's copy, and a C-local predicate is the only thing available.
    """
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        is not None
    )


def build_topic_graph(transcript: TranscriptReady) -> int:
    """Build this meeting's topic graph and participation matrix, and store it.

    Returns how many topics were stored.

    Idempotent: a re-run replaces every topic of the meeting, and its edges,
    evidence and participation go with it through ``ON DELETE CASCADE``. So do
    the ``gap_related_topics`` rows of any gap already raised — nothing raises
    one yet (#35), and when something does it has to be rebuilt in the same
    run.

    Only a consenting participant's speech is analysed (privacy.md section 5),
    and only they appear in the matrix. Speech with no participant behind it is
    left out as well: whether its speaker consented is unknown, and unknown is
    not yes.

    Extraction runs between two transactions rather than inside one. It is the
    slow step, and a connection held open across it is a connection nobody
    else can use.
    """
    meeting_id = transcript.meeting_id

    with session_scope() as session:
        if session.get(Meeting, meeting_id) is None:
            raise ValueError(f"{meeting_id}: meeting row not found")
        participants = list(
            session.scalars(
                select(Participant.id)
                .where(Participant.meeting_id == meeting_id, Participant.consented.is_(True))
                .order_by(Participant.id)
            )
        )
        rows = session.execute(
            select(Utterance.id, Utterance.participant_id)
            .join(Participant, Participant.id == Utterance.participant_id)
            .where(Utterance.meeting_id == meeting_id, Participant.consented.is_(True))
        ).all()
        # The inner join already dropped utterances with no participant; the
        # filter is for the type checker, which cannot see through a join.
        speaker_of = {
            utterance_id: participant_id
            for utterance_id, participant_id in rows
            if participant_id is not None
        }

    analysed = [(u.id, u.text) for u in transcript.utterances if u.id in speaker_of]
    extractor = get_entity_extractor()
    entities = extractor.extract(analysed)
    extractor_version = extractor.model_version

    topics = graph.build_topics(entities, [utterance_id for utterance_id, _ in analysed])
    edges = graph.co_occurrence_edges(topics)
    scores = graph.centrality(topics, edges)
    position = {u.id: index for index, u in enumerate(transcript.utterances)}

    with session_scope() as session:
        session.execute(delete(GapTopic).where(GapTopic.meeting_id == meeting_id))

        topic_ids = {topic.key: new_id(ids.TOPIC) for topic in topics}
        session.add_all(
            GapTopic(
                id=topic_ids[topic.key],
                meeting_id=meeting_id,
                label=topic.label,
                extractor_version=extractor_version,
                centrality=scores[topic.key].pagerank,
                betweenness=scores[topic.key].betweenness,
            )
            for topic in topics
        )
        # The topics have to exist before anything points at them; the unit of
        # work only orders tables it can see a relationship between.
        session.flush()

        session.add_all(
            GapTopicUtterance(
                topic_id=topic_ids[topic.key],
                utterance_id=utterance_id,
                position=position[utterance_id],
            )
            for topic in topics
            for utterance_id in topic.utterance_ids
        )
        session.add_all(
            GapTopicEdge(
                meeting_id=meeting_id,
                source_topic_id=topic_ids[edge.source],
                target_topic_id=topic_ids[edge.target],
                relation=edge.relation,
                weight=edge.weight,
            )
            for edge in edges
        )
        session.add_all(
            GapParticipation(topic_id=topic_ids[topic.key], participant_id=participant, spoke=spoke)
            for topic in topics
            for participant, spoke in graph.participation(topic, speaker_of, participants).items()
        )

    # Counts only: a topic label is transcript text, and no log line carries any.
    log.info(
        "gap_topic_graph_built",
        meeting_id=meeting_id,
        utterances=len(analysed),
        excluded=len(transcript.utterances) - len(analysed),
        topics=len(topics),
        edges=len(edges),
        extractor=extractor_version,
    )
    return len(topics)


def detect_gaps(meeting_id: str) -> int:
    """Compare the stored topic graph against the meeting's domain template and
    store what the meeting did not settle. Returns how many gaps stand.

    Steps 6 and 7 of docs/modules/gap.md. Reads rows rather than the transcript:
    everything comparison needs is already in ``gap_topics`` and
    ``gap_participation``, so this runs without the extractor and can be run
    again on its own when somebody changes the template.

    **A re-run keeps the gap rows it already raised.** They are recognised by
    ``(meeting_id, template_key, template_item_key)`` and updated in place, so
    ``id`` survives — a link somebody sent to a gap still opens it — and so does
    ``dismissed_at``, which is a person's judgement and the input ADR 0006's
    threshold tuning reads. A row this run did not produce is deleted: the
    meeting covers that item now, and a gap that is no longer a gap should not
    sit in the table waiting to be counted. Only template rows are touched;
    a gap found from the graph alone carries no template key and is left alone.

    Call it after ``build_topic_graph``, which deletes and rebuilds the topics —
    the cascade takes ``gap_related_topics`` with them, and this puts them back.
    Between the two a report read shows its gaps with no related topics, which
    is why the pipeline runs them back to back and the publish comes after.
    """
    settings = get_settings()

    with session_scope() as session:
        if session.get(Meeting, meeting_id) is None:
            raise ValueError(f"{meeting_id}: meeting row not found")

        chosen = template.get_template(selected_template_key(session, meeting_id))
        topics = _topic_views(session, meeting_id)
        findings = detect.compare(chosen, topics, _thresholds(settings))
        _store_gaps(session, meeting_id, chosen, findings)

    # Counts and keys only. A gap title is composed from a template file and a
    # topic label is transcript text; neither goes in a log line.
    log.info(
        "gap_detection_complete",
        meeting_id=meeting_id,
        template=chosen.key,
        template_version=chosen.version,
        topics=len(topics),
        gaps=len(findings),
        high=sum(1 for finding in findings if finding.severity == "high"),
    )
    return len(findings)


def selected_template_key(session: Session, meeting_id: str) -> str:
    """Which template this meeting is compared against.

    The stored override if there is one, otherwise ``default_template``. An
    override naming a template file that no longer exists falls back to the
    default rather than failing the meeting's pipeline — a deleted template is
    the deployment's problem, and refusing to analyse the meeting does not make
    it less so. The warning names the key, which is a template name and not
    meeting content.
    """
    settings = get_settings()
    override = session.get(GapMeetingTemplate, meeting_id)
    if override is None:
        return settings.default_template

    if override.template_key not in template.load_templates():
        log.warning(
            "gap_template_override_unknown",
            meeting_id=meeting_id,
            template=override.template_key,
            falling_back_to=settings.default_template,
        )
        return settings.default_template
    return override.template_key


def set_template(session: Session, meeting_id: str, template_key: str) -> str:
    """Point this meeting at a template and re-compare against it.

    ``get_template`` rejects a key no file defines, so the row that lands is
    always resolvable. Detection runs again immediately — the alternative is a
    screen where choosing a template appears to do nothing until the meeting is
    reprocessed.

    It does **not** republish ``autune.gap.completed``. E scores the meeting the
    pipeline produced, and a template somebody is trying out on S20 should not
    silently rewrite that; the endpoint returns the key and the report endpoint
    shows the new gaps.
    """
    chosen = template.get_template(template_key)

    row = session.get(GapMeetingTemplate, meeting_id)
    if row is None:
        session.add(GapMeetingTemplate(meeting_id=meeting_id, template_key=chosen.key))
    else:
        row.template_key = chosen.key
    session.flush()

    return chosen.key


def available_templates() -> list[TemplateRead]:
    """Every template a meeting can be compared against, for the S20 rail."""
    return [
        TemplateRead(key=one.key, name=one.name, version=one.version, items=len(one.items))
        for one in template.available()
    ]


def _thresholds(settings: GapSettings) -> detect.Thresholds:
    """``config`` values as the shape ``detect`` takes.

    Kept here so ``detect`` stays a pure function of its arguments — a test can
    score a finding against numbers it names, without a settings object and
    without the environment deciding the answer.
    """
    return detect.Thresholds(
        high=settings.risk_threshold,
        medium=settings.medium_threshold,
        partial_centrality=settings.partial_centrality,
        partial_damping=settings.partial_damping,
        weight_template=settings.weight_template,
        weight_coverage=settings.weight_coverage,
        weight_participation=settings.weight_participation,
    )


def _topic_views(session: Session, meeting_id: str) -> list[detect.TopicView]:
    """The meeting's topics with the one aggregate comparison reads off each:
    how much of the consenting room was silent on it.

    A share over a topic, not a total along a person — the distinction
    docs/architecture/privacy.md section 3 turns on. A topic nobody was
    considered for carries ``None``, which ``detect.score`` drops rather than
    reading as "everybody spoke".
    """
    topics = _topics_in_reading_order(session, meeting_id)
    said = _spoke_by_person(session, meeting_id, [topic.id for topic in topics])

    views = []
    for topic in topics:
        people = said.get(topic.id, {})
        silent = (
            None
            if not people
            else sum(1 for spoke_here in people.values() if not spoke_here) / len(people)
        )
        views.append(
            detect.TopicView(
                id=topic.id, label=topic.label, centrality=topic.centrality, silent_share=silent
            )
        )
    return views


def _store_gaps(
    session: Session,
    meeting_id: str,
    chosen: template.Template,
    findings: list[detect.Finding],
) -> None:
    """Write the findings, keeping the identity of gaps already raised.

    See ``detect_gaps`` for why the row is updated rather than replaced. Related
    topics are rewritten every run: ``build_topic_graph`` deletes the meeting's
    topics before this runs, and the rows pointing at them went with the
    cascade.
    """
    stored = {
        (row.template_key, row.template_item_key): row
        for row in session.scalars(
            select(GapGap).where(GapGap.meeting_id == meeting_id, GapGap.template_key.is_not(None))
        )
    }
    produced: set[tuple[str | None, str | None]] = set()

    for finding in findings:
        identity = (chosen.key, finding.item_key)
        produced.add(identity)

        gap = stored.get(identity)
        if gap is None:
            gap = GapGap(
                id=new_id(ids.GAP),
                meeting_id=meeting_id,
                template_key=chosen.key,
                template_item_key=finding.item_key,
            )
            session.add(gap)

        gap.category = finding.category
        gap.title = finding.title
        gap.severity = finding.severity
        gap.risk_score = finding.risk_score
        gap.template_item = finding.template_item
        gap.template_version = chosen.version
        gap.suggested_question = finding.question
        session.flush()

        session.execute(delete(GapRelatedTopic).where(GapRelatedTopic.gap_id == gap.id))
        session.add_all(
            GapRelatedTopic(gap_id=gap.id, topic_id=topic_id) for topic_id in finding.topic_ids
        )

    for stale, gap in stored.items():
        if stale not in produced:
            session.delete(gap)


def publish_report(meeting_id: str) -> GapReport:
    """Publish what is stored for the meeting as ``GapReport`` on
    ``autune.gap.completed``, and return it.

    Read from the rows after their transaction has committed, not from what
    ``build_topic_graph`` held in memory. What E receives and what the report
    screen reads later are then one thing read one way, and a subscriber that
    looks something up by a topic id finds the row already there.

    C names the event and never its consumer — ``autune_core.publish`` finds
    E's task (#145). Safe to run twice: E records one report per meeting, and a
    second publish of the same rows is the same report.
    """
    with session_scope() as session:
        report = build_report(session, meeting_id)

    publish(GAP_COMPLETED, report.model_dump(mode="json"))
    log.info(
        "gap_report_published",
        meeting_id=meeting_id,
        topics=len(report.topics),
        gaps=len(report.gaps),
    )
    return report


def build_report(session: Session, meeting_id: str) -> GapReport:
    """The meeting's ``GapReport``, assembled from ``gap_*`` rows.

    - Topics come most central first — the order a reader should meet them in —
      each with its evidence in meeting order. Topics that carried the meeting
      equally come in the order the meeting reached them. Without a rule for
      ties the order would follow the random topic ids, and a redelivered task
      would publish the same report shuffled.
    - Participation is lists of participant ids and nothing else. ``spoke`` and
      ``silent`` together are every consenting person, because silence is what a
      gap is raised on (privacy.md section 3 is why there is no number). One
      person is one entry however many voices diarization split them into —
      see ``_people`` — and having spoken as any of them puts them in
      ``spoke``: recording speech as silence is the worse of the two mistakes.
    - A dismissed gap is not reported. Its row stays for threshold tuning, but
      the team has said it is wrong, and E counting it would score the meeting
      on a gap nobody believes in.
    """
    topics = _topics_in_reading_order(session, meeting_id)
    topic_ids = [topic.id for topic in topics]

    evidence: dict[str, list[str]] = defaultdict(list)
    for topic_id, utterance_id in session.execute(
        select(GapTopicUtterance.topic_id, GapTopicUtterance.utterance_id)
        .where(GapTopicUtterance.topic_id.in_(topic_ids))
        .order_by(GapTopicUtterance.position)
    ).all():
        evidence[topic_id].append(utterance_id)

    said = _spoke_by_person(session, meeting_id, topic_ids)

    gaps = list(
        session.scalars(
            select(GapGap)
            .where(GapGap.meeting_id == meeting_id, GapGap.dismissed_at.is_(None))
            .order_by(GapGap.risk_score.desc(), GapGap.id)
        )
    )
    related: dict[str, list[str]] = defaultdict(list)
    for gap_id, topic_id in session.execute(
        select(GapRelatedTopic.gap_id, GapRelatedTopic.topic_id)
        .where(GapRelatedTopic.gap_id.in_([gap.id for gap in gaps]))
        .order_by(GapRelatedTopic.id)
    ).all():
        related[gap_id].append(topic_id)

    return GapReport(
        meeting_id=meeting_id,
        topics=[
            Topic(
                id=topic.id,
                label=topic.label,
                centrality=topic.centrality,
                utterance_ids=evidence[topic.id],
            )
            for topic in topics
        ],
        participation=[
            Participation(
                topic_id=topic.id,
                spoke=sorted(who for who, spoke_here in said[topic.id].items() if spoke_here),
                silent=sorted(who for who, spoke_here in said[topic.id].items() if not spoke_here),
            )
            for topic in topics
        ],
        gaps=[
            Gap(
                id=gap.id,
                category=gap.category,
                title=gap.title,
                severity=GapSeverity(gap.severity),
                risk_score=gap.risk_score,
                template_item=gap.template_item,
                related_topic_ids=related[gap.id],
                suggested_question=gap.suggested_question,
            )
            for gap in gaps
        ],
    )


def topic_graph(session: Session, meeting_id: str) -> TopicGraphRead:
    """The meeting's topic graph for drawing: the nodes, and what joins them.

    The report answers "what did this meeting cover and who was silent on it";
    this answers "what did it look like". They read the same rows, and the
    nodes come back in the same order, so S20 can put the picture beside the
    list without reconciling two orderings.

    Edges come back as stored, both directions of a symmetric relation
    included — ``schemas.TopicEdgeRead`` says why that is not collapsed here.
    They are ordered strongest first, then by where their endpoints sit in the
    node order, then by the relation, because ``gap_topic_edges.id`` is an
    autoincrement that a re-run reassigns: ordering by it would redraw the same
    graph in a different order every time the meeting was reprocessed.

    The relation is part of the key rather than a leftover tie. Today
    ``co_occurrence_edges`` writes one relation per direction and the first
    three fields are already unique, but ``uq_gap_topic_edges`` allows
    ``(a, b, "depends_on")`` beside ``(a, b, "co_occurs")`` and #32 is about to
    write exactly that; with equal weights the tie would fall through to the
    scan order, which is the shuffle this ordering exists to prevent. Raised in
    review of #220.

    A meeting whose graph has not been built answers with empty lists. The
    caller has already established that the meeting exists; nothing analysed
    yet is a state, not a missing resource.

    **Edges are asked for by the node ids this read already has**, the way
    ``build_report`` asks for evidence and participation. The two reads are
    separate statements, and on PostgreSQL's default isolation a re-run of
    ``build_topic_graph`` committing between them is visible: the topics are
    the set that was deleted and the edges are the new set, which reference
    topic ids this read has never seen. Subscripting ``rank`` with one of those
    raised ``KeyError`` — a 500 from the endpoint whose contract is "empty is a
    state, not an error" — and the screen polling while the pipeline reprocesses
    a meeting is exactly who would hit it. Filtering in the query makes that
    case a consistent older graph with the edges it can still account for, and
    leaves ``rank`` safe by construction. Raised in review of #220.
    """
    topics = _topics_in_reading_order(session, meeting_id)
    rank = {topic.id: index for index, topic in enumerate(topics)}
    edges = sorted(
        session.scalars(
            select(GapTopicEdge).where(
                GapTopicEdge.meeting_id == meeting_id,
                GapTopicEdge.source_topic_id.in_(rank),
                GapTopicEdge.target_topic_id.in_(rank),
            )
        ),
        key=lambda edge: (
            -edge.weight,
            rank[edge.source_topic_id],
            rank[edge.target_topic_id],
            edge.relation,
        ),
    )
    return TopicGraphRead(
        meeting_id=meeting_id,
        nodes=[
            TopicNodeRead(
                id=topic.id,
                label=topic.label,
                centrality=topic.centrality,
                betweenness=topic.betweenness,
            )
            for topic in topics
        ],
        edges=[
            TopicEdgeRead(
                source_topic_id=edge.source_topic_id,
                target_topic_id=edge.target_topic_id,
                relation=edge.relation,
                weight=edge.weight,
            )
            for edge in edges
        ],
    )


def _spoke_by_person(
    session: Session, meeting_id: str, topic_ids: list[str]
) -> dict[str, dict[str, bool]]:
    """Topic id -> person -> whether they spoke on it.

    One person is one entry however many participant rows diarization split
    them into; ``_people`` decides who that is, and having spoken as any of
    them counts as having spoken. Read by the report, which shows the two sides
    per topic, and by ``detect_gaps``, which reads how much of the room stayed
    silent on one. Both need the same merge, and a second copy of it would be a
    second chance to get the withdrawal case below wrong.

    Coverage, never volume: the value is a boolean and totalling it along a
    person rather than along a topic is the speaking ratio that
    docs/architecture/privacy.md section 3 keeps private to its subject.
    """
    person = _people(session, meeting_id)
    said: dict[str, dict[str, bool]] = defaultdict(dict)

    for topic_id, participant_id, spoke_here in session.execute(
        select(
            GapParticipation.topic_id, GapParticipation.participant_id, GapParticipation.spoke
        ).where(GapParticipation.topic_id.in_(topic_ids))
    ).all():
        who = person.get(participant_id)
        if who is None:
            # Not, or no longer, consenting. `_people` is built from consenting
            # rows only, but a `gap_participation` row outlives a withdrawal
            # until the next run — so defaulting to the participant's own id
            # put somebody who had withdrawn back into the report, and this
            # function is written to be re-run over stored rows. The default
            # has to be the closed one. Raised in review of #164.
            continue
        said[topic_id][who] = said[topic_id].get(who, False) or spoke_here

    return said


def _topics_in_reading_order(session: Session, meeting_id: str) -> list[GapTopic]:
    """The meeting's topics, most central first.

    Ties go to the topic the meeting reached first, then to the label. Topic
    ids are random, so without the tie-break the same stored graph would come
    back in a different order on every read — and the report E receives and
    the graph the screen draws would disagree about which topic came second.

    ``NULLS LAST`` is spelled out because the two engines disagree by default:
    ascending order puts NULL first on SQLite and last on PostgreSQL. A topic
    with no evidence rows sorts last either way now — it is the one a reader
    can check least, so it does not belong above one the meeting can be quoted
    on. Worth saying because the unit tests run on SQLite and production runs
    on PostgreSQL, so the implicit version was a rule no test could have
    caught. Raised in review of #220.
    """
    first_said = (
        select(
            GapTopicUtterance.topic_id,
            func.min(GapTopicUtterance.position).label("position"),
        )
        .group_by(GapTopicUtterance.topic_id)
        .subquery()
    )
    return list(
        session.scalars(
            select(GapTopic)
            .outerjoin(first_said, first_said.c.topic_id == GapTopic.id)
            .where(GapTopic.meeting_id == meeting_id)
            .order_by(
                GapTopic.centrality.desc(),
                nulls_last(first_said.c.position),
                GapTopic.label,
            )
        )
    )


def _people(session: Session, meeting_id: str) -> dict[str, str]:
    """Participant id -> the participant id that stands for that person in the
    report.

    Module A makes one participant row per speaker label, and splitting one
    voice into two clusters is diarization's characteristic failure. Once
    identification (#6) fills ``user_id``, one person can own two rows — and
    the matrix, stored per row, would have them speak on a topic as one and
    stay silent on it as the other. A gap raised on that silence would be a
    false statement about somebody who spoke. Raised in review of #164.

    Rows sharing a ``user_id`` are one person, represented by the smallest of
    their participant ids; a row with no ``user_id`` stands for itself.

    **Represented by a participant id, not by the user id.** A user id is the
    same in every meeting, so a report carrying one could be joined across
    meetings into a record of one person's silences; a participant id belongs
    to this meeting only (docs/modules/gap.md). Grouping by ``user_id`` fixes
    the double count without giving that up.
    """
    rows = session.execute(
        select(Participant.id, Participant.user_id).where(
            Participant.meeting_id == meeting_id, Participant.consented.is_(True)
        )
    ).all()
    first: dict[str, str] = {}
    for participant_id, user_id in rows:
        if user_id is not None:
            first[user_id] = min(first.get(user_id, participant_id), participant_id)
    return {
        participant_id: participant_id if user_id is None else first[user_id]
        for participant_id, user_id in rows
    }
