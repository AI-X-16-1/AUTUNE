"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/shared/ui/Button";

import { attestConsent } from "../api";
import { useLiveSession } from "../hooks/useLiveSession";
import { useMicrophone } from "../hooks/useMicrophone";
import type { RecordingState } from "../types";
import { LiveTranscript } from "./LiveTranscript";

/**
 * S13 with a microphone behind it: the container the route mounts.
 *
 * `LiveTranscript` is deliberately stateless — it draws whatever it is handed.
 * This component owns what changes: the microphone and its levels
 * (`useMicrophone`), the socket, the rows, the recorder and the upload
 * (`useLiveSession`), and the gate before any of it starts.
 *
 * The gate is the minimum that keeps consent honest before S10's per-attendee
 * table exists: one checkbox, which calls `POST /meetings/{id}/consent`, and
 * `start` is disabled until it succeeds. Without it recording still works and
 * modules B and C analyse nothing — the screen says so in those words.
 *
 * Speaker actions still log. Assigning a speaker writes `speaker_id` on a
 * shared entity, which only module A does and only through `/api/audio`; the
 * endpoint is not there yet, and a live row has no speaker to assign anyway.
 */
export function LiveMeetingScreen({ meetingId }: { meetingId: string }) {
  const router = useRouter();
  const microphone = useMicrophone();
  const live = useLiveSession(meetingId, microphone.stream);
  const [consented, setConsented] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);

  // Once the microphone is open and the session is idle, hand it to the
  // socket/recorder. This must not run during render -- `live.start()` sets
  // state -- so it lives in an effect, keyed on the stable `live.start`
  // (a `useCallback` over `[meetingId, stream, abandon]`). The hook's own
  // re-entry guard makes a StrictMode double-invoke harmless.
  const livePhase = live.phase;
  const liveStart = live.start;
  const microphoneStop = microphone.stop;
  useEffect(() => {
    if (microphone.stream && livePhase === "idle") void liveStart();
  }, [microphone.stream, livePhase, liveStart]);

  // A refusal (no live view is ever coming, per useLiveSession) means the
  // recording was already abandoned; the microphone is the one thing left
  // for the screen itself to release, since `onStop` is not coming.
  useEffect(() => {
    if (livePhase === "error") microphoneStop();
  }, [livePhase, microphoneStop]);

  // A dropped tab mid-recording loses whatever the recorder has not
  // uploaded yet; ask before that happens.
  useEffect(() => {
    if (live.phase !== "recording" && live.phase !== "paused") return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [live.phase]);

  // Navigation is a side effect, not something to run during render.
  useEffect(() => {
    if (live.phase === "done") router.push(`/meetings/${meetingId}`);
  }, [live.phase, meetingId, router]);

  const onConsent = async (checked: boolean) => {
    setConsented(false);
    setConsentError(null);
    if (!checked) return;
    try {
      await attestConsent(meetingId);
      setConsented(true);
    } catch (caught) {
      setConsentError(caught instanceof Error ? caught.message : "동의를 기록하지 못했습니다.");
    }
  };

  const onStart = async () => {
    if (live.phase === "error") live.reset();
    await microphone.start();
  };

  const onStop = async () => {
    await live.stop();
    microphone.stop();
  };

  if (live.phase === "done") {
    return null;
  }

  if (live.phase === "idle" || live.phase === "error") {
    return (
      <main className="mx-auto max-w-[720px] p-[var(--space-page)]">
        <h1
          className="text-ink-strong"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          녹음 시작
        </h1>
        <label className="mt-6 flex items-start gap-3" style={{ fontSize: "var(--text-body)" }}>
          <input
            type="checkbox"
            checked={consented}
            onChange={(event) => void onConsent(event.target.checked)}
          />
          <span>
            이 회의 참석자 전원이 녹음과 분석에 동의했습니다.
            <span className="block text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
              체크하지 않으면 녹음은 되지만 액션 아이템과 갭은 분석되지 않습니다.
            </span>
          </span>
        </label>
        {consentError && (
          <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
            {consentError}
          </p>
        )}
        {(microphone.error ?? live.error) && (
          <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
            {microphone.error ?? live.error}
          </p>
        )}
        <div className="mt-6">
          <Button tone="primary" disabled={!consented} onClick={() => void onStart()}>
            녹음 시작
          </Button>
        </div>
      </main>
    );
  }

  if (live.phase === "uploading" || live.phase === "upload_failed") {
    return (
      <main className="mx-auto max-w-[720px] p-[var(--space-page)]">
        <p style={{ fontSize: "var(--text-body)" }}>
          {live.phase === "uploading" ? "녹음을 올리는 중입니다…" : "업로드에 실패했습니다."}
        </p>
        {live.phase === "upload_failed" && (
          <>
            <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
              {live.error} 녹음은 아직 이 탭에 있습니다. 탭을 닫지 마세요.
            </p>
            <Button tone="primary" onClick={() => void live.retryUpload()}>
              다시 올리기
            </Button>
          </>
        )}
      </main>
    );
  }

  // Only "connecting" / "recording" / "paused" remain: the session has not
  // reached ready yet, or it is live.
  const state: RecordingState = live.phase === "paused" ? "paused" : "recording";

  return (
    <>
      {live.liveLost && (
        <p role="status" className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
          라이브 전사가 끊겼습니다. 녹음은 계속되고, 정지하면 전체 녹음이 올라갑니다.
        </p>
      )}
      <LiveTranscript
        state={state}
        rows={live.rows}
        elapsedSeconds={live.elapsedSeconds}
        levels={microphone.levels}
        onPause={live.pause}
        onResume={live.resume}
        onStop={() => void onStop()}
        onAssignSpeaker={(speaker) => console.log("assign speaker", { meetingId, speaker })}
        onEnterSpeakerName={(speaker) => console.log("enter speaker name", { meetingId, speaker })}
        onSendConfirmation={(speaker) => console.log("send confirmation DM", { meetingId, speaker })}
      />
    </>
  );
}
