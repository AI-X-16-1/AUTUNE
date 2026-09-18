"""Runs the hand-labeled evaluation set against the real pipeline.

Unlike the integration tests, which pin the extractor to ``fake`` for
determinism, this calls ``service`` with whatever ``AUTUNE_GAP_NER_IMPL`` the
environment has configured — the point of the harness is to measure the actual
spaCy + noun-run + template stack, not the plumbing around it. A run under
``fake`` produces a number about a hand-written vocabulary rather than about a
model, and ``__main__`` says so at the top of the report rather than letting it
be quoted.

Needs:

- A Postgres reachable via ``autune_core.Settings``, migrated to ``heads``
  (``uv run alembic -c infra/alembic.ini upgrade heads``). See
  docs/engineering/environments.md for the docker-compose database.
- The spaCy pipeline, which is the ``local-models`` extra:
  ``uv sync --package autune-gap --extra local-models``.

Each case gets its own team, deleted once scored, so a run leaves no synthetic
data behind — every ``gap_*`` row and every utterance reaches deletion through
``meetings.id``, which is the same premise the deletion test rests on.

**Nothing here leaves the machine.** No integration is called, so nothing
touches ``packages/integrations``' outbound boundary; the transcripts are
authored fixture text rather than meeting content; and the report prints
template item keys and counts, never a topic label or a line of a transcript.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from autune_contracts import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    validate_major_version,
)
from autune_contracts import (
    Utterance as UtterancePayload,
)
from autune_core import Meeting, Participant, Team, Utterance, session_scope
from autune_gap import service
from autune_gap.eval.dataset import DEFAULT_DATASET, EvalCase, load_cases
from autune_gap.eval.metrics import CaseScore, Report, classify_false_positive, score
from autune_gap.graph import topic_key
from autune_gap.models import GapGap, GapRelatedTopic, GapTopic
from autune_gap.template import Template, get_template

_SURFACED = "high"
"""The only severity a reader sees by default (modules/gap/CLAUDE.md), and so
the band precision is measured over."""


def run_case(case: EvalCase) -> CaseScore:
    with session_scope() as s:
        team = Team(name=f"eval-{case.id}")
        s.add(team)
        s.flush()
        team_id = team.id

    try:
        meeting_id = _seed(team_id, case)

        with session_scope() as s:
            chosen = get_template(service.set_template(s, meeting_id, case.template_key))

        transcript = _transcript(meeting_id, case)
        # The same two checks `tasks.on_transcript_ready` makes before it hands
        # a transcript to the module. The harness goes in through `service`
        # rather than the task, and skipping them would let the fixtures drift
        # into a shape the real entry point would have refused.
        validate_major_version(transcript)
        transcript.require_privacy_guarantees()

        service.build_topic_graph(transcript)
        service.detect_gaps(meeting_id)

        with session_scope() as s:
            rows = list(s.scalars(select(GapGap).where(GapGap.meeting_id == meeting_id)))
            # Labels are transcript-derived, so they are used to classify and
            # never printed -- see this module's docstring and `__main__`.
            labels = frozenset(
                topic_key(label)
                for label in s.scalars(
                    select(GapTopic.label).where(GapTopic.meeting_id == meeting_id)
                )
            )
            # Which gaps point at a topic. A missing item was inferred from the
            # absence of one and points at none; a partial one points at the
            # topic it was inferred from. The row does not store the coverage
            # state, and this is the same fact read off the link table.
            with_topics = set(
                s.scalars(
                    select(GapRelatedTopic.gap_id).where(
                        GapRelatedTopic.gap_id.in_([row.id for row in rows])
                    )
                )
            )
            topics = (
                s.scalar(
                    select(func.count())
                    .select_from(GapTopic)
                    .where(GapTopic.meeting_id == meeting_id)
                )
                or 0
            )

        raised_high = frozenset(
            row.template_item_key
            for row in rows
            if row.severity == _SURFACED and row.template_item_key is not None
        )
        raised_partial = frozenset(
            row.template_item_key
            for row in rows
            if row.severity == _SURFACED
            and row.template_item_key is not None
            and row.id in with_topics
        )

        return CaseScore(
            case_id=case.id,
            template_key=case.template_key,
            real=case.real_gaps,
            raised_high=raised_high,
            raised_any=frozenset(
                row.template_item_key for row in rows if row.template_item_key is not None
            ),
            raised_partial=raised_partial,
            topics=topics,
            fp_cause=_causes(case, chosen, raised_high, raised_partial, labels),
        )
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def _causes(
    case: EvalCase,
    chosen: Template,
    raised_high: frozenset[str],
    raised_partial: frozenset[str],
    labels: frozenset[str],
) -> dict[str, str]:
    """Why each false positive happened, for the ones the case labeled evidence
    for.

    A case with no ``evidence`` gets an empty map and its false positives are
    reported as unclassified — the split is a claim about labeled data and
    should not be inferred for a case nobody labeled.
    """
    if not case.evidence:
        return {}

    keywords = {item.key: item.keywords for item in chosen.items}
    return {
        item_key: classify_false_positive(
            partial=item_key in raised_partial,
            expected=tuple(topic_key(term) for term in case.evidence.get(item_key, ())),
            topic_labels=labels,
            keywords=keywords.get(item_key, ()),
        )
        for item_key in raised_high - case.real_gaps
    }


def _seed(team_id: str, case: EvalCase) -> str:
    """The meeting as module A would leave it: participants who consented, and
    utterances already masked.

    Everybody consents. A participant who did not is excluded from analysis
    (privacy.md section 5) and the case would then be scoring a different
    meeting from the one it labeled — whether C may speak about a meeting it
    only half saw is issue #248, not something to fold into a precision figure.
    """
    with session_scope() as s:
        meeting = Meeting(team_id=team_id, title=f"eval {case.id}", status="analyzing")
        s.add(meeting)
        s.flush()

        people = {}
        for name in case.speakers:
            person = Participant(meeting_id=meeting.id, speaker_label=name, consented=True)
            s.add(person)
            s.flush()
            people[name] = person.id

        for index, line in enumerate(case.lines):
            s.add(
                Utterance(
                    id=f"utt_{meeting.id}_{index}",
                    meeting_id=meeting.id,
                    participant_id=people[line.speaker],
                    speaker_label=line.speaker,
                    start_sec=float(index),
                    end_sec=float(index) + 1,
                    text=line.text,
                )
            )
        return meeting.id


def _transcript(meeting_id: str, case: EvalCase) -> TranscriptReady:
    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            UtterancePayload(
                id=f"utt_{meeting_id}_{index}",
                speaker=line.speaker,
                start=float(index),
                end=float(index) + 1,
                text=line.text,
                confidence=0.9,
            )
            for index, line in enumerate(case.lines)
        ],
        metadata=TranscriptMetadata(
            duration=float(len(case.lines)),
            participants=case.speakers,
            source=TranscriptSource.FILE_UPLOAD,
            language="ko",
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    )


def run_all(dataset: str = DEFAULT_DATASET) -> Report:
    return score([run_case(case) for case in load_cases(dataset)])
