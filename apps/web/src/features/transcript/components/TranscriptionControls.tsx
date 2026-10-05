"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui/Button";

import { cancelTranscription, restartTranscription } from "../api";
import type { MeetingDetail } from "../types";

/**
 * S12's cancel and restart (spec 2026-10-02, section 4), in two parts:
 * `CancelTranscription` sits in the screen's header, top right, where a
 * person looking to stop a run finds it at once; `RestartNotice` sits with
 * the stages, because it explains what the stages show. The S12 mockup has
 * neither, so both use the screen's existing tokens and components.
 *
 * Which buttons show is the server's decision (`MeetingDetail.stalled`,
 * `restartable`, `cancellable`), so the rule lives in one place. After a
 * press nothing is reloaded here: `useMeeting` polls every three seconds
 * while the meeting is `analyzing`, and the next poll shows the result.
 */

/** One press at a time, its refusal, and a reset when the server's view moves. */
function useAction(meeting: MeetingDetail) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A press leaves `pending` set until the server's view changes. A restart
  // keeps the meeting `cancellable` (the new attempt is queued), so without
  // this 처리 중단 would stay disabled until a remount. A refusal shown for
  // the old state is stale once the state moves, so it goes too.
  useEffect(() => {
    setPending(false);
    setError(null);
  }, [meeting.stalled, meeting.restartable, meeting.cancellable]);

  const run = async (
    call: (id: string) => Promise<unknown>,
  ): Promise<boolean> => {
    setPending(true);
    setError(null);
    try {
      await call(meeting.meeting_id);
      return true;
    } catch (caught) {
      setError(errorCopy(caught));
      setPending(false);
      return false;
    }
  };
  return { pending, error, run };
}

function Refusal({ error }: { error: string | null }) {
  if (error === null) return null;
  return (
    <p
      role="alert"
      style={{
        fontSize: "var(--text-meta)",
        color: "var(--color-signal-critical)",
      }}
    >
      {error}
    </p>
  );
}

/**
 * 처리 중단, top right. Asks in place rather than through `window.confirm`:
 * a browser dialog blocks the page, and the question needs one sentence of
 * context the dialog would lose (the original on the server is deleted).
 */
export function CancelTranscription({ meeting }: { meeting: MeetingDetail }) {
  const [confirming, setConfirming] = useState(false);
  const { pending, error, run } = useAction(meeting);
  useEffect(
    () => setConfirming(false),
    [meeting.stalled, meeting.restartable, meeting.cancellable],
  );

  if (!meeting.cancellable) return null;

  return (
    <div className="flex shrink-0 flex-col items-end gap-2 text-right">
      {confirming ? (
        <>
          <p
            className="max-w-[260px] text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-meta)" }}
          >
            처리를 중단하면 서버에 있는 원본 녹음이 삭제됩니다.
          </p>
          <div className="flex gap-2">
            <Button
              tone="text"
              size="compact"
              disabled={pending}
              onClick={() => setConfirming(false)}
            >
              계속 진행
            </Button>
            <Button
              tone="destructiveText"
              size="compact"
              disabled={pending}
              onClick={async () => {
                if (await run(cancelTranscription)) setConfirming(false);
              }}
            >
              중단하기
            </Button>
          </div>
        </>
      ) : (
        <Button
          tone="secondary"
          size="compact"
          disabled={pending}
          onClick={() => setConfirming(true)}
        >
          처리 중단
        </Button>
      )}
      <Refusal error={error} />
    </div>
  );
}

/**
 * Why a stalled run stopped, and 다시 시작 when the upload is still on the
 * server. A job that never left the queue (`stage` still null) is named as a
 * long wait, not a silent worker: that is the case a lost message makes.
 */
export function RestartNotice({ meeting }: { meeting: MeetingDetail }) {
  const { pending, error, run } = useAction(meeting);

  if (!meeting.stalled) return null;

  const why =
    meeting.stage === null
      ? "15분 넘게 처리가 시작되지 않았습니다. 서버가 요청을 놓쳤을 수 있습니다."
      : "2분 넘게 처리 응답이 없습니다. 서버가 다시 시작되었을 수 있습니다.";

  return (
    <div
      className="mb-3 flex flex-col gap-2"
      style={{ fontSize: "var(--text-meta)" }}
    >
      <p role="status" className="text-[var(--color-ink-strong)]">
        {meeting.restartable
          ? why
          : "서버에 녹음 파일이 남아 있지 않아 다시 시작할 수 없습니다. 처리를 중단한 뒤 녹음을 다시 올려 주세요. 실시간으로 녹음한 회의는 올릴 파일이 없을 수 있습니다."}
      </p>
      {meeting.restartable && (
        <div>
          <Button
            tone="primary"
            size="compact"
            disabled={pending}
            onClick={() => void run(restartTranscription)}
          >
            다시 시작
          </Button>
        </div>
      )}
      <Refusal error={error} />
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
  const known =
    caught instanceof ApiError ? ERROR_COPY[caught.code] : undefined;
  return known ?? "요청을 처리하지 못했습니다.";
}
