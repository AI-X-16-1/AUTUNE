import { useEffect, useState } from "react";

import { Button } from "@/shared/ui";

import type { SpeakerCandidate, TeamMember } from "../types";

/**
 * Putting a name to a voice the pipeline separated but could not identify.
 *
 * Two controls are live -- confirming a candidate, and picking from the
 * team -- and two are placeholders that explain, in their `title`, what is
 * missing before they can open. Neither live control guesses: a similarity
 * score high enough to show is not high enough to write into the record,
 * because the cost of being wrong is a commitment filed under somebody who
 * never made it. A candidate is drawn next to the label with its similarity;
 * confirming is a click, and it is the click that writes the name, never the
 * score.
 *
 * "확인 DM 보내기" would also enrol the voice for next time (S16), which is why
 * it was the lead action when this component had only placeholders. It is
 * disabled and last now: the candidate confirm is the one click that
 * actually identifies somebody today, so it takes the `tone="text"` slot,
 * and `ui-spec.md`'s one-primary-per-screen rule is still respected --
 * S13's primary is 녹음 종료 in the right rail, the action you cannot take back.
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
  pending = false,
  onAssign,
}: {
  speaker: string;
  candidate?: SpeakerCandidate | null;
  members?: TeamMember[];
  /** True while a confirm this prompt (or a sibling one) started is in
   * flight. Disables every live control, closing the double-click hole a
   * second click mid-request would otherwise open. */
  pending?: boolean;
  onAssign?: (userId: string) => void;
}) {
  // The select is controlled so a failed pick can be undone. A success drops
  // this whole entry from the caller's list -- the speaker is no longer
  // unidentified -- so this component unmounts before `picked` would matter;
  // a failure leaves it mounted with `pending` back at false, which is the
  // signal to put the placeholder back rather than keep showing a name that
  // was never written.
  const [picked, setPicked] = useState("");
  useEffect(() => {
    if (!pending) setPicked("");
  }, [pending]);

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
          disabled={pending}
          onClick={() => onAssign?.(candidate.user_id)}
        >
          {candidate.name} 맞습니다
        </Button>
      )}
      <select
        aria-label={`${speaker} 화자 지정`}
        value={picked}
        disabled={pending}
        onChange={(event) => {
          const userId = event.target.value;
          setPicked(userId);
          if (userId) onAssign?.(userId);
        }}
        className="focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]"
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
