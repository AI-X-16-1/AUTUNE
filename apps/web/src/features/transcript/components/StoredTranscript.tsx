"use client";

import { useEffect, useMemo, useState } from "react";

import { timecode } from "../format";
import { useSpeakers } from "../hooks/useSpeakers";
import { useTranscript } from "../hooks/useTranscript";
import { useUtteranceAnchor } from "../hooks/useUtteranceAnchor";
import type { SpeakerCandidate, SpeakerEntry, TeamMember, Utterance, UtteranceKind } from "../types";
import { PiiReportModal, readSelection, ReportButton, type Selected } from "./PiiReport";
import { AssignedSpeaker } from "./AssignedSpeaker";
import { TranscriptRow } from "./TranscriptRow";
import { type PickableMember, UnidentifiedSpeaker } from "./UnidentifiedSpeaker";

/**
 * A finished meeting's transcript, read back from what was stored.
 *
 * The S15 transcript tab rather than S13. `LiveTranscript` in this folder draws
 * a meeting as it is being recorded — a frame that glows, a level meter, rows
 * that must never move once written. None of that applies here: the meeting is
 * over, re-reading is the point, and there is no recording to show the state of.
 * Sharing `TranscriptRow` between the two is deliberate; drawing a line of
 * transcript two ways is how the same redaction comes to look like two things.
 *
 * **Empty is a state, not an error.** Utterances are written in one transaction
 * when the task finishes, so a meeting still being processed has none. Saying
 * "아직 없습니다" is honest; a spinner that never resolves is not.
 *
 * Classifications are not fetched. The tag on a row comes from module B and this
 * feature may not call another module's endpoints — `CLAUDE.md` in this folder,
 * and the boundary exists so B can change its shape without breaking A's screen.
 * A page that wants tags joins the two itself.
 *
 * **This is the only screen with an identification prompt.** No `Participant`
 * row exists for a meeting until `persist_transcript` writes them (via
 * `_participants_for`), in the same transaction as the utterances — so a
 * meeting still recording or still being processed has none, and `GET
 * /speakers` returns `[]` for it, not entries with `candidate: null`.
 * `LiveTranscript` carries no prompt at all for exactly that reason (see its
 * own docstring). By the time a transcript is stored, the participants exist
 * and the worker has written their observation vectors, so this is also the
 * one screen where a candidate can appear.
 *
 * **Which of the branches below show it.** Only the last one, past
 * "utterances exist". The loading and empty-transcript branches deliberately
 * have none — same reason as above, there is nothing to identify yet. The
 * error branch is different: it hides a speaker list that may have loaded
 * successfully, on account of the *transcript* fetch alone failing. That is
 * a real gap, not a deliberate one, and it is not fixed here.
 *
 * **Selecting text offers S30.** A selection inside one line shows a floating
 * "개인정보 신고"; reporting masks the span on the server, and the transcript
 * is read again so the line shows the redaction it now stores.
 *
 * **A link can name a line.** `/meetings/{id}#{utterance_id}` — S20's evidence
 * links — scrolls to that row once the transcript is in and tints it briefly
 * (`useUtteranceAnchor`). Each row is wrapped rather than given the id itself,
 * so `TranscriptRow`, shared with the live screen, stays as it is.
 */
export function StoredTranscript({
  meetingId,
  teamId,
  kinds = {},
}: {
  meetingId: string;
  teamId: string | null;
  /** Classification per utterance id, if the page has already fetched them. */
  kinds?: Record<string, UtteranceKind>;
}) {
  const [version, setVersion] = useState(0);
  const state = useTranscript(meetingId, version);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [reporting, setReporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // A selection that goes away takes the floating button with it, unless the
  // modal is already open.
  useEffect(() => {
    const onChange = () => {
      if (!reporting && window.getSelection()?.isCollapsed) setSelected(null);
    };
    document.addEventListener("selectionchange", onChange);
    return () => document.removeEventListener("selectionchange", onChange);
  }, [reporting]);
  const {
    speakers,
    speakersError,
    members,
    membersError,
    assign,
    name,
    unassign,
    assignError,
    pending,
  } = useSpeakers(meetingId, teamId);
  const unidentified = speakers.filter((entry) => entry.user_id === null);
  const assigned = speakers.filter((entry) => entry.user_id !== null);
  const nameOf = speakerNames(speakers, members);
  const pick = unassigned(speakers, members);
  const ids = useMemo(
    () => (state.status === "ready" ? state.utterances.map((utterance) => utterance.id) : null),
    [state],
  );
  const anchored = useUtteranceAnchor(ids);

  if (state.status === "loading") {
    return (
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        전사를 불러오는 중입니다…
      </p>
    );
  }

  if (state.status === "error") {
    return (
      <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
        {state.message}
      </p>
    );
  }

  if (state.utterances.length === 0) {
    return (
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        아직 전사된 내용이 없습니다. 분석이 끝나면 여기에 표시됩니다.
      </p>
    );
  }

  return (
    <section aria-label="회의 전사" onMouseUp={() => setSelected(readSelection())}>
      {notice && (
        <p role="status" style={{ fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" }}>
          {notice}
        </p>
      )}

      {speakersError && (
        <p
          role="alert"
          style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}
        >
          {speakersError}
        </p>
      )}

      {membersError && (
        <p
          role="alert"
          style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}
        >
          {membersError}
        </p>
      )}

      {assigned.map((entry) => (
        <AssignedSpeaker
          key={entry.speaker_label}
          speaker={entry.speaker_label}
          name={nameOf(entry.speaker_label, entry.user_id)}
          pending={pending}
          onUnassign={() => void unassign(entry.speaker_label)}
        />
      ))}

      {unidentified.map((entry) => (
        <UnidentifiedSpeaker
          key={entry.speaker_label}
          speaker={entry.speaker_label}
          candidate={pick.candidate(entry.candidate)}
          displayName={entry.display_name}
          members={pick.members}
          membersError={membersError}
          pending={pending}
          onAssign={(userId) => void assign(entry.speaker_label, userId)}
          onName={(typed) => void name(entry.speaker_label, typed)}
        />
      ))}

      {assignError && (
        <p
          role="alert"
          style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}
        >
          {assignError}
        </p>
      )}

      {state.utterances.map((utterance) => (
        <div
          key={utterance.id}
          id={utterance.id}
          // No fade: there is no motion token for one, and a literal would be
          // a token change (this folder's CLAUDE.md).
          style={{
            background: anchored === utterance.id ? "var(--color-accent-selection)" : undefined,
          }}
        >
          <TranscriptRow
            row={{ utterance, kind: kinds[utterance.id] }}
            name={nameOf(utterance.speaker, utterance.speaker_id)}
          />
        </div>
      ))}

      {selected && !reporting && <ReportButton selected={selected} onOpen={() => setReporting(true)} />}

      {selected && reporting && (
        <ReportModal
          meetingId={meetingId}
          selected={selected}
          utterances={state.utterances}
          nameOf={nameOf}
          onClose={() => {
            setReporting(false);
            setSelected(null);
          }}
          onReported={(message) => {
            setReporting(false);
            setSelected(null);
            window.getSelection()?.removeAllRanges();
            setNotice(message);
            setVersion((value) => value + 1);
          }}
        />
      )}
    </section>
  );
}

/**
 * Who each line was said by, by name, from the two lists this screen already
 * holds.
 *
 * A row used to print its diarization label ("화자 1") whatever happened to
 * it, and the transcript is read once, so an assignment made on this screen
 * changed nothing below it: the request succeeded (204) and the line still
 * said "화자 1". `useSpeakers` re-reads the label → person list after every
 * assignment and already has the team's members, so the name comes from those
 * and appears the moment the assignment lands, with no second transcript read.
 *
 * The label's current assignment wins over the row's stored `speaker_id`,
 * which is only as fresh as the transcript read. A person no longer on the
 * team has no name to show; the line keeps its label. A label with no person
 * but a name typed for this meeting (`display_name`) shows that name.
 */
export function speakerNames(
  speakers: SpeakerEntry[],
  members: TeamMember[],
): (label: string, storedId: string | null | undefined) => string | null {
  const nameById = new Map(members.map((member) => [member.user_id, member.name]));
  const idByLabel = new Map(speakers.map((entry) => [entry.speaker_label, entry.user_id]));
  const typedByLabel = new Map(
    speakers.flatMap((entry) => (entry.display_name ? [[entry.speaker_label, entry.display_name]] : [])),
  );
  return (label, storedId) => {
    const id = idByLabel.has(label) ? idByLabel.get(label) : storedId;
    if (id) return nameById.get(id) ?? null;
    return typedByLabel.get(label) ?? null;
  };
}

/**
 * What the speaker prompts offer: the members nobody is put to yet first, then
 * those already put to a speaker in this meeting, last and marked with that
 * label. Once 화자 1 is 김민경, 화자 2 and 화자 3 lead with the other two. A
 * candidate who is already somebody else's voice is not suggested again.
 *
 * An assigned member stays in the list, behind a confirmation in
 * `UnidentifiedSpeaker`, because diarization sometimes splits one voice into
 * two labels and both have to reach the same account for "내 발화 삭제" to
 * find every line (#912 review). Removing them, as this first did, closed
 * that path.
 */
export function unassigned(
  speakers: SpeakerEntry[],
  members: TeamMember[],
): {
  members: PickableMember[];
  candidate: (candidate: SpeakerCandidate | null) => SpeakerCandidate | null;
} {
  const labelById = new Map(
    speakers.flatMap((entry) => (entry.user_id ? [[entry.user_id, entry.speaker_label]] : [])),
  );
  return {
    members: [
      ...members.filter((member) => !labelById.has(member.user_id)),
      ...members.flatMap((member) => {
        const label = labelById.get(member.user_id);
        return label ? [{ ...member, assignedTo: label }] : [];
      }),
    ],
    candidate: (candidate) => (candidate && !labelById.has(candidate.user_id) ? candidate : null),
  };
}

function ReportModal({
  meetingId,
  selected,
  utterances,
  nameOf,
  onClose,
  onReported,
}: {
  meetingId: string;
  selected: Selected;
  utterances: Utterance[];
  nameOf: (label: string, storedId: string | null | undefined) => string | null;
  onClose: () => void;
  onReported: (message: string) => void;
}) {
  const utterance = utterances.find((row) => row.id === selected.utteranceId);
  if (!utterance) return null;
  return (
    <PiiReportModal
      meetingId={meetingId}
      selected={selected}
      context={{
        time: timecode(utterance.start),
        speaker: nameOf(utterance.speaker, utterance.speaker_id) ?? utterance.speaker,
        text: utterance.text,
      }}
      onClose={onClose}
      onReported={(result) =>
        onReported(
          `${result.occurrences}곳을 마스킹했습니다.` +
            (result.republished ? " 요약·할 일·갭 분석에 다시 반영됩니다." : "") +
            (result.rule ? ` 앞으로 ${result.rule} 형태는 자동으로 가립니다.` : ""),
        )
      }
    />
  );
}
