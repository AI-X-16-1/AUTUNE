"use client";

import { useState } from "react";

import { cancelTranscription, restartTranscription } from "../api";
import type { MeetingDetail } from "../types";

/**
 * S12's cancel and restart (spec 2026-10-02, section 4). The S12 mockup has
 * neither, so this uses the screen's existing tokens and nothing new.
 *
 * Which buttons show is the server's decision (`MeetingDetail.stalled`,
 * `restartable`, `cancellable`), so the rule lives in one place. After a
 * press nothing is reloaded here: `useMeeting` polls every three seconds
 * while the meeting is `analyzing`, and the next poll shows the result.
 *
 * Cancel asks in place rather than through `window.confirm`: a browser
 * dialog blocks the page, and the question needs one sentence of context
 * the dialog would lose (the original on the server is deleted).
 */
export function TranscriptionControls({ meeting }: { meeting: MeetingDetail }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!meeting.cancellable && !meeting.stalled) return null;

  const run = async (call: (id: string) => Promise<unknown>) => {
    setPending(true);
    setError(null);
    try {
      await call(meeting.meeting_id);
      setConfirming(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "요청을 처리하지 못했습니다.");
      setPending(false);
    }
  };

  const meta = { fontSize: "var(--text-meta)" } as const;
  const link = "text-[var(--color-accent-default)] disabled:opacity-50";

  return (
    <div className="mt-3 flex flex-col gap-2" style={meta}>
      {meeting.stalled && (
        <p role="status" className="text-[var(--color-ink-strong)]">
          {meeting.restartable
            ? "2분 넘게 처리에 응답이 없어요. 서버가 다시 시작됐을 수 있어요."
            : "서버에 녹음 파일이 남아 있지 않아 다시 시작할 수 없어요. 처리를 취소하고 녹음을 다시 올려 주세요. 실시간으로 녹음한 회의라면 다시 올릴 파일이 없을 수도 있어요."}
        </p>
      )}

      {confirming ? (
        <p className="text-[var(--color-ink-strong)]">
          처리를 취소하면 서버에 있는 원본 녹음이 삭제돼요.{" "}
          <button
            type="button"
            className={link}
            disabled={pending}
            onClick={() => run(cancelTranscription)}
          >
            취소하기
          </button>{" "}
          <button
            type="button"
            className="text-[var(--color-ink-muted)]"
            disabled={pending}
            onClick={() => setConfirming(false)}
          >
            계속 진행
          </button>
        </p>
      ) : (
        <p className="flex gap-3">
          {meeting.stalled && meeting.restartable && (
            <button
              type="button"
              className={link}
              disabled={pending}
              onClick={() => run(restartTranscription)}
            >
              다시 시작
            </button>
          )}
          {meeting.cancellable && (
            <button
              type="button"
              className="text-[var(--color-ink-muted)] disabled:opacity-50"
              disabled={pending}
              onClick={() => setConfirming(true)}
            >
              처리 취소
            </button>
          )}
        </p>
      )}

      {error !== null && (
        <p role="alert" style={{ color: "var(--color-signal-critical)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
