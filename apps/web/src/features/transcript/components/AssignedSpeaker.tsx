import { useEffect, useState } from "react";

import { Button } from "@/shared/ui";

/**
 * A speaker already put to a team member, with the way back.
 *
 * Before this, an assigned speaker dropped out of the prompts and nothing on
 * the screen could reach it again: a wrong pick stayed, and with voice
 * profiles on it kept teaching the candidate list the wrong voice. 지정 해제
 * sends `DELETE /meetings/{id}/speakers/{label}`; the label is then
 * unidentified again and comes back as an `UnidentifiedSpeaker` prompt.
 *
 * Asks in place first, like 처리 중단: the question needs its one sentence --
 * the voice this meeting taught that person's profile is deleted with the
 * assignment -- and a browser dialog would block the page and lose it.
 */
export function AssignedSpeaker({
  speaker,
  name,
  pending = false,
  onUnassign,
}: {
  speaker: string;
  /** The member's name, or null when they are no longer on the team. */
  name: string | null;
  pending?: boolean;
  onUnassign: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  // A finished write (success or failure) closes the question; on success
  // this row is gone anyway, on failure the error shows beside the list.
  useEffect(() => {
    if (!pending) setConfirming(false);
  }, [pending]);
  const who = name ?? "팀에 없는 사람";

  return (
    <div
      className="flex flex-wrap items-center gap-2"
      style={{ paddingBlock: "var(--space-12)", fontSize: "var(--text-status)" }}
    >
      <span style={{ color: "var(--color-ink-muted)" }}>
        {speaker} · {who}
      </span>
      {confirming ? (
        <>
          <span role="status" style={{ color: "var(--color-ink-strong)" }}>
            {speaker}의 지정을 해제합니다. 이 회의에서 익힌 {who}의 목소리도 지워집니다.
          </span>
          <Button tone="destructiveText" size="compact" disabled={pending} onClick={onUnassign}>
            해제하기
          </Button>
          <Button tone="quiet" size="compact" disabled={pending} onClick={() => setConfirming(false)}>
            취소
          </Button>
        </>
      ) : (
        <Button tone="quiet" size="compact" disabled={pending} onClick={() => setConfirming(true)}>
          지정 해제
        </Button>
      )}
    </div>
  );
}
