import { useEffect, useState } from "react";

import { Button } from "@/shared/ui";

import type { SpeakerCandidate, TeamMember } from "../types";

/**
 * Putting a name to a voice the pipeline separated but could not identify.
 *
 * Three controls are live -- confirming a candidate, picking from the team,
 * and typing a name -- and one is a placeholder that explains, in its
 * `title`, what is missing before it can open. Neither live control guesses: a similarity
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
 * **직접 입력 is for a voice with no account on the team** -- a guest,
 * someone from another company. The name is kept for this meeting only
 * (`aud_speaker_names`): no person is attached, no voice is enrolled, and the
 * published transcript still carries no `speaker_id`, so no other module
 * learns it. The line says so, rather than letting a typed name pass for an
 * identification. A named speaker stays in this list so that a typo can be
 * fixed and a member can still be picked instead.
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
  displayName,
  members,
  membersError,
  pending = false,
  onAssign,
  onName,
}: {
  speaker: string;
  candidate?: SpeakerCandidate | null;
  /** A name already typed for this speaker, for this meeting only. */
  displayName?: string | null;
  members?: TeamMember[];
  /** Set when `GET /teams/{id}/members` failed. An empty `members` with no
   * error reads as "nobody else on this team"; an empty `members` *with*
   * one means the picker has nothing to offer only because the request
   * failed, not because the team is small -- the select is disabled and
   * says so in its `title` rather than sitting there silently empty. */
  membersError?: string | null;
  /** True while a confirm this prompt (or a sibling one) started is in
   * flight. Disables every live control, closing the double-click hole a
   * second click mid-request would otherwise open. */
  pending?: boolean;
  onAssign?: (userId: string) => void;
  /** Called with the trimmed name when 저장 is clicked. */
  onName?: (name: string) => void;
}) {
  // What the select holds, which is a choice and not yet an assignment: the
  // "지정" button is what sends it. A success drops this whole entry from the
  // caller's list -- the speaker is no longer unidentified -- so this
  // component unmounts before `picked` would matter; a failure leaves it
  // mounted with `pending` back at false, and the effect puts the placeholder
  // back rather than keep showing a name that was never written.
  const [picked, setPicked] = useState("");
  useEffect(() => {
    if (!pending) setPicked("");
  }, [pending]);
  // `null` while the text field is closed. Closed again once a save lands
  // (`displayName` changes); a failure leaves it open with what was typed.
  const [typed, setTyped] = useState<string | null>(null);
  useEffect(() => {
    setTyped(null);
  }, [displayName]);
  const trimmed = typed?.trim() ?? "";

  return (
    <div
      className="flex flex-wrap items-center gap-2"
      style={{
        paddingBlock: "var(--space-12)",
        color: "var(--color-signal-attention)",
        fontSize: "var(--text-status)",
      }}
    >
      {displayName ? (
        <span style={{ color: "var(--color-ink-muted)" }}>
          {speaker} · {displayName} (이 회의에서만 표시)
        </span>
      ) : candidate ? (
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
        disabled={pending || Boolean(membersError)}
        title={membersError ?? undefined}
        // Selecting is not confirming. On Windows Chrome an arrow key on a
        // closed select fires `change`, so a keyboard user who tabs here and
        // presses ↓ once used to assign the first team member outright
        // (@PARKJAEKYUNG0525 on #370). There is no endpoint to undo it, and
        // with voice profiles on it puts one person's voice under another
        // person's name. The button below is the confirmation.
        onChange={(event) => setPicked(event.target.value)}
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
        tone="text"
        size="compact"
        disabled={pending || !picked}
        onClick={() => {
          if (picked) onAssign?.(picked);
        }}
      >
        지정
      </Button>
      {typed === null ? (
        <Button
          tone="quiet"
          size="compact"
          disabled={pending}
          title="팀에 계정이 없는 참석자의 이름을 이 회의에서만 표시합니다"
          onClick={() => setTyped(displayName ?? "")}
        >
          {displayName ? "이름 고치기" : "직접 입력"}
        </Button>
      ) : (
        <>
          <input
            aria-label={`${speaker} 이름 직접 입력`}
            value={typed}
            maxLength={50}
            placeholder="이 회의에서만 표시할 이름"
            disabled={pending}
            autoFocus
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && trimmed) onName?.(trimmed);
              if (event.key === "Escape") setTyped(null);
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
          />
          <Button
            tone="text"
            size="compact"
            disabled={pending || !trimmed}
            onClick={() => onName?.(trimmed)}
          >
            저장
          </Button>
          <Button tone="quiet" size="compact" disabled={pending} onClick={() => setTyped(null)}>
            취소
          </Button>
        </>
      )}
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
