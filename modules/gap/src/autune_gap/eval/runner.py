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
from autune_gap import detect, service
from autune_gap.eval.dataset import DEFAULT_DATASET, EvalCase, load_cases
from autune_gap.eval.metrics import CaseScore, Report, classify_false_positive, score
from autune_gap.graph import topic_key
from autune_gap.models import GapGap, GapTopic
from autune_gap.template import Template, TemplateItem, get_template

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
            # The row stores the coverage state now (#303), so the split is
            # read off it. It used to be derived from `gap_related_topics` --
            # a gap with no linked topic was one raised on an absent item --
            # and that derivation stopped being true in the same PR: an item
            # the meeting only *said*, with no topic behind it, is partial and
            # links to nothing. Every such gap read as missing, disagreed with
            # its own title, and stopped the run.
            _check_coverage_agrees(rows, chosen)
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
            and row.coverage == detect.Coverage.PARTIAL
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


class HarnessInconsistencyError(Exception):
    """The harness's two readings of the same fact disagree, so the report it
    would print cannot be trusted. Louder than a wrong number."""


def _check_coverage_agrees(rows: list[GapGap], chosen: Template) -> None:
    """Cross-check the stored partial/missing split against the stored title.

    The split used to be derived from ``gap_related_topics`` — a gap with no
    linked topic was raised on an absent item — because the row did not carry
    the state. That derivation had a failure mode that looked exactly like a
    real result: ``build_topic_graph`` deletes the meeting's topics before
    ``detect_gaps`` runs and the cascade takes the link rows with them, so **an
    empty link table read as "every gap is missing"** (``service.detect_gaps``
    says as much in its own docstring). The headline claim this harness makes —
    that the centrality threshold is not what costs precision — is exactly that
    shape, and nothing in the report would have told the two apart.

    ``gap_gaps.coverage`` is what this docstring used to call the real fix, and
    #303 added it. The derivation is gone, and with it the cascade failure
    mode. What remains is the guard: ``gap_gaps.title`` is an independent
    second reading — ``detect`` composes it from the same coverage state, the
    two wordings differ, and the row stores both — so if they disagree the run
    stops rather than printing a conclusion built on one of them.

    **A row with no coverage stops the run too.** The column is nullable
    because rows written before its migration have nothing to put there, and a
    ``None`` read as "not partial" is a wrong number rather than a refusal.

    Raised in review of #277; re-aimed at the stored column in review of #303.
    """
    items = {item.key: item for item in chosen.items}

    for row in rows:
        item = items.get(row.template_item_key or "")
        if item is None or row.template_item_key is None:
            continue

        from_title = _coverage_from_title(row.title, item)
        if from_title is None:
            continue

        if row.coverage is None:
            raise HarnessInconsistencyError(
                f"{row.meeting_id}: gap on {row.template_item_key!r} stores no coverage, and "
                f"its title reads {from_title!r}. The cause split in this report would be "
                "guessed, so it is not printed."
            )

        if from_title != row.coverage:
            raise HarnessInconsistencyError(
                f"{row.meeting_id}: gap on {row.template_item_key!r} stores {row.coverage!r} "
                f"and its title reads {from_title!r}. The cause split in this report would be "
                "wrong, so it is not printed."
            )


def _coverage_from_title(title: str, item: TemplateItem) -> str | None:
    """``"partial"``, ``"missing"``, or ``None`` when the title is neither —
    a gap raised by something other than template comparison, which this
    harness has nothing to say about."""
    if title == detect.MISSING_TITLE.format(item=item.item):
        return "missing"
    if title == detect.PARTIAL_TITLE.format(item=item.item):
        return "partial"
    return None


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
    """The meeting as module A will leave it once #190 is settled: participants
    who consented, and utterances already masked.

    **Not as module A leaves one today.** ``persistence.py:199`` creates every
    participant with ``consented=False`` and its own docstring says nothing in
    the repository ever sets it True (#190), so a meeting in this shape is one
    no real transcript reaches yet. Said plainly because the earlier wording
    ("as module A would leave it") reads as a description of production and
    would have somebody expecting real meetings to arrive analysable.

    Everybody consents here on purpose: a participant who did not is excluded
    from analysis (privacy.md section 5) and the case would then be scoring a
    different meeting from the one it labeled. Whether C may speak about a
    meeting it only half saw is #248, and folding it into a precision figure
    would measure two things at once. Raised in review of #277.
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
