"""Business logic for module C: Gap Detection.

Owner: 박재경. See docs/modules/gap.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``gap_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from autune_contracts.enums import GapSeverity
from autune_contracts.events import GAP_COMPLETED
from autune_contracts.gap import Gap, GapReport, Participation, Topic
from autune_core import Meeting, Participant, Utterance, get_logger, ids, new_id, session_scope
from autune_core.events import publish
from autune_gap import graph
from autune_gap.models import (
    GapGap,
    GapParticipation,
    GapRelatedTopic,
    GapTopic,
    GapTopicEdge,
    GapTopicUtterance,
)
from autune_gap.pipeline import get_entity_extractor
from autune_gap.schemas import TopicEdgeRead, TopicGraphRead, TopicNodeRead

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from autune_contracts import TranscriptReady

log = get_logger(__name__)


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
    """
    topics = _topics_in_reading_order(session, meeting_id)
    rank = {topic.id: index for index, topic in enumerate(topics)}
    edges = sorted(
        session.scalars(select(GapTopicEdge).where(GapTopicEdge.meeting_id == meeting_id)),
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


def _topics_in_reading_order(session: Session, meeting_id: str) -> list[GapTopic]:
    """The meeting's topics, most central first.

    Ties go to the topic the meeting reached first, then to the label. Topic
    ids are random, so without the tie-break the same stored graph would come
    back in a different order on every read — and the report E receives and
    the graph the screen draws would disagree about which topic came second.
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
            .order_by(GapTopic.centrality.desc(), first_said.c.position, GapTopic.label)
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
