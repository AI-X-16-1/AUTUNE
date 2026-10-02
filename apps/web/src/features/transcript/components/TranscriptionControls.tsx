"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";

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

  // A press leaves `pending` set until the server's view changes. A restart
  // keeps the meeting `cancellable` (the new attempt is queued), so without
  // this the 처리 중단 button would stay disabled until a remount. A refusal
  // shown for the old state is stale once the state moves, so it goes too.
  useEffect(() => {
    setPending(false);
    setConfirming(false);
    setError(null);
  }, [meeting.stalled, meeting.restartable, meeting.cancellable]);

  if (!meeting.cancellable && !meeting.stalled) return null;

  const run = async (call: (id: string) => Promise<unknown>) => {
    setPending(true);
    setError(null);
    try {
      await call(meeting.meeting_id);
      setConfirming(false);
    } catch (caught) {
      setError(errorCopy(caught));
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
            ? "2분 넘게 처리 응답이 없습니다. 서버가 다시 시작되었을 수 있습니다."
            : "서버에 녹음 파일이 남아 있지 않아 다시 시작할 수 없습니다. 처리를 중단한 뒤 녹음을 다시 올려 주세요. 실시간으로 녹음한 회의는 올릴 파일이 없을 수 있습니다."}
        </p>
      )}

      {confirming ? (
        <p className="text-[var(--color-ink-strong)]">
          처리를 중단하면 서버에 있는 원본 녹음이 삭제됩니다.{" "}
          <button
            type="button"
            className={link}
            disabled={pending}
            onClick={() => run(cancelTranscription)}
          >
            중단하기
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
              처리 중단
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

/**
 * Korean copy for a refusal, by the server's error code. The server's own
 * message is English and names the meeting id, so it is never shown.
 */
const ERROR_COPY: Record<string, string> = {
  nothing_to_cancel: "이미 끝났거나 중단된 처리입니다.",
  not_stalled: "처리가 다시 응답하고 있어 다시 시작하지 않았습니다.",
  recording_gone: "서버에 녹음 파일이 남아 있지 않아 다시 시작할 수 없습니다.",
  enqueue_failed: "처리를 다시 시작하지 못했습니다. 녹음을 다시 올려 주세요.",
};

function errorCopy(caught: unknown): string {
  const known = caught instanceof ApiError ? ERROR_COPY[caught.code] : undefined;
  return known ?? "요청을 처리하지 못했습니다.";
}
