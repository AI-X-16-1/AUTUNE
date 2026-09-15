"""Business logic for module C: Gap Detection.

Owner: 박재경. See docs/modules/gap.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``gap_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from autune_core import Meeting, Participant, Utterance, get_logger, ids, new_id, session_scope
from autune_gap import graph
from autune_gap.models import GapParticipation, GapTopic, GapTopicEdge, GapTopicUtterance
from autune_gap.pipeline import get_entity_extractor

if TYPE_CHECKING:
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
