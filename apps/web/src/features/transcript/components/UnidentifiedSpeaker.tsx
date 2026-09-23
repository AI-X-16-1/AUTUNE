import { Button } from "@/shared/ui";

import type { SpeakerCandidate, TeamMember } from "../types";

/**
 * The three ways to put a name to a voice the pipeline separated but could not
 * identify.
 *
 * All three end with a person saying who it is. None of them guesses: a
 * similarity score high enough to show is not high enough to write into the
 * record, because the cost of being wrong is a commitment filed under somebody
 * who never made it. A candidate is drawn next to the label with its
 * similarity; confirming is a click, and it is the click that writes the
 * name, never the score.
 *
 * "확인 DM 보내기" is the one that also enrols the voice for next time (S16), so
 * it leads. It is a text button rather than a fill: `ui-spec.md` allows one
 * primary per screen, and on S13 that is 녹음 종료 in the right rail — the
 * action you cannot take back.
 *
 * **No utterance count.** This line used to read `화자 2 · 발화 41건`, which is
 * a per-person speech volume wearing a number instead of a name. Everyone in
 * the room knows who 화자 2 is, so the anonymity is not real —
 * `privacy.md` section 3 names this exact shape in its Forbidden list: "An
 * 'anonymized' distribution across a small meeting — in a four-person meeting,
 * a distribution identifies everyone." The count was not doing any work either:
 * the prompt asks who a voice belongs to, and how much it said does not help
 * answer that.
 */
export function UnidentifiedSpeaker({
  speaker,
  candidate,
  members,
  onAssign,
}: {
  speaker: string;
  candidate?: SpeakerCandidate | null;
  members?: TeamMember[];
  onAssign?: (userId: string) => void;
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      style={{
        paddingBlock: "var(--space-12)",
        color: "var(--color-signal-attention)",
        fontSize: "var(--text-status)",
      }}
    >
      {candidate ? (
        <span>
          {speaker} · 후보 {candidate.name} · 유사도 {candidate.similarity.toFixed(2)}
        </span>
      ) : (
        <span>{speaker} · 누구인지 확인이 필요합니다</span>
      )}
      {candidate && (
        <Button
          tone="text"
          size="compact"
          onClick={() => onAssign?.(candidate.user_id)}
        >
          {candidate.name} 맞습니다
        </Button>
      )}
      <select
        defaultValue=""
        onChange={(event) => {
          if (event.target.value) onAssign?.(event.target.value);
        }}
        style={{
          height: "var(--control-h-compact)",
          paddingInline: "var(--control-px-compact)",
          fontSize: "var(--control-text-compact)",
          borderRadius: "var(--radius)",
          border: "1px solid var(--color-hairline)",
          background: "var(--color-surface-sunken)",
          color: "var(--color-ink-strong)",
        }}
      >
        <option value="">참석자 중에서 지정</option>
        {(members ?? []).map((member) => (
          <option key={member.user_id} value={member.user_id}>
            {member.name}
          </option>
        ))}
      </select>
      <Button
        tone="quiet"
        size="compact"
        disabled
        title="계정이 없는 참석자를 어떻게 기록할지 정해지면 열립니다"
      >
        직접 입력
      </Button>
      <Button
        tone="quiet"
        size="compact"
        disabled
        title="Slack 워크스페이스를 연결하면 열립니다"
      >
        확인 DM 보내기
      </Button>
    </div>
  );
}
