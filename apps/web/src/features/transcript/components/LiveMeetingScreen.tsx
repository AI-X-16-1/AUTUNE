"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/shared/ui/Button";

import { attestConsent } from "../api";
import { useEndingSoon } from "../hooks/useEndingSoon";
import { useLiveSession, type LivePhase } from "../hooks/useLiveSession";
import { useMeetingTeam } from "../hooks/useMeetingTeam";
import { useMeetingTitle } from "../hooks/useMeetingTitle";
import { useMicrophone, type Microphone } from "../hooks/useMicrophone";
import { forgetPlannedEnd } from "../plannedEnd";
import type { RecordingState } from "../types";
import { LiveTopBar } from "./LiveTopBar";
import { LiveTranscript } from "./LiveTranscript";
import { UploadFailed } from "./UploadFailed";

/** The phases in which closing the tab loses audio that is not yet uploaded. */
const LEAVING_LOSES_AUDIO = new Set<LivePhase>([
  "connecting",
  "recording",
  "paused",
  "uploading",
  "upload_failed",
]);

/**
 * S13 with a microphone behind it: the container the route mounts.
 *
 * `LiveTranscript` is deliberately stateless — it draws whatever it is handed.
 * This component owns what changes: the microphone and its levels
 * (`useMicrophone`), the socket, the rows, the recorder and the upload
 * (`useLiveSession`), and the gate before any of it starts.
 *
 * The gate is the minimum that keeps consent honest before S10's per-attendee
 * table exists: one checkbox, which calls `POST /meetings/{id}/consent`. It
 * does not block `start` — recording works without it and modules B and C
 * analyse nothing — and the screen says so in those words. The button waits
 * only while a consent request is in flight, so a tick is not lost to a
 * start that races it.
 *
 * The gate is also S10's microphone check. The input opens for the level
 * meter as soon as the gate shows (`useMicrophone().preview`), so the person
 * sees their voice move the bars and can switch device before anything
 * records; "녹음 시작" hands that same stream to the session. A refused
 * permission is drawn as its own state with a "권한 허용" retry, and
 * "파일 업로드로 대신" sends the meeting to the upload form (`?meeting=`,
 * which the backend accepts for a `scheduled` meeting).
 *
 * Speaker identification does not reach this screen. `LiveTranscript` has no
 * prompt during a recording — see its own docstring for why (no `Participant`
 * row exists until the meeting is processed) — so this screen does not need
 * the meeting's `team_id` and does not poll for it.
 *
 * **Five minutes before the planned end** (S14's small cut, #1147) the screen
 * draws what the page put in `endingSoon`, above the transcript. This screen
 * owns only the *when*: the end this tab was told on the new-meeting screen
 * (`plannedEnd`) and one timer to five minutes before it (`useEndingSoon`),
 * counted only while the recording is under way. What is drawn is another
 * module's, so the page supplies it and is handed the meeting's team — read
 * once, at that moment, and never for a meeting with no planned end. With no
 * planned end, or nothing in the slot, the screen is as it was.
 */
export function LiveMeetingScreen({
  meetingId,
  notice,
  endingSoon,
}: {
  meetingId: string;
  /** Drawn above the gate's consent row. The page fills it; see its file. */
  notice?: ReactNode;
  /** Drawn above the transcript from five minutes before the planned end. */
  endingSoon?: (teamId: string) => ReactNode;
}) {
  const router = useRouter();
  const microphone = useMicrophone();
  const title = useMeetingTitle(meetingId);
  const live = useLiveSession(meetingId, microphone.stream);
  const [consented, setConsented] = useState(false);
  const [consentPending, setConsentPending] = useState(false);
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

  // S10's level meter: open the input as soon as the gate is up, once. A
  // grant arriving later (from the site settings, after a refusal) reopens it
  // inside the hook.
  const microphonePreview = microphone.preview;
  const previewAsked = useRef(false);
  useEffect(() => {
    if (livePhase !== "idle" || previewAsked.current) return;
    previewAsked.current = true;
    void microphonePreview();
  }, [livePhase, microphonePreview]);

  // A refusal (no live view is ever coming, per useLiveSession) means the
  // recording was already abandoned; the microphone is the one thing left
  // for the screen itself to release, since `onStop` is not coming. The gate
  // is back up afterwards, so its meter reopens rather than reading
  // "마이크를 여는 중…" over a closed input.
  useEffect(() => {
    if (livePhase !== "error") return;
    microphoneStop();
    void microphonePreview();
  }, [livePhase, microphoneStop, microphonePreview]);

  // A dropped tab loses whatever the recorder has not uploaded yet -- while
  // it is connecting, recording, paused, or the upload is still in flight or
  // waiting for a retry; ask before that happens.
  useEffect(() => {
    if (!LEAVING_LOSES_AUDIO.has(live.phase)) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [live.phase]);

  // Navigation is a side effect, not something to run during render.
  useEffect(() => {
    if (live.phase !== "done") return;
    // The recording is in; the end this tab was told has nothing left to time.
    forgetPlannedEnd(meetingId);
    router.push(`/meetings/${meetingId}`);
  }, [live.phase, meetingId, router]);

  // S14's small cut. Timed only while the recording is under way, and the
  // team is read only once the moment has come and there is a slot to fill.
  const ending = useEndingSoon(
    meetingId,
    endingSoon !== undefined && (livePhase === "recording" || livePhase === "paused"),
  );
  const endingTeamId = useMeetingTeam(meetingId, ending);

  const onConsent = async (checked: boolean) => {
    setConsented(false);
    setConsentError(null);
    if (!checked) return;
    setConsentPending(true);
    try {
      await attestConsent(meetingId);
      setConsented(true);
    } catch (caught) {
      setConsentError(caught instanceof Error ? caught.message : "동의를 기록하지 못했습니다.");
    } finally {
      setConsentPending(false);
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

  const recording: RecordingState | null =
    live.phase === "recording" || live.phase === "connecting"
      ? "recording"
      : live.phase === "paused"
        ? "paused"
        : null;

  // S13 is drawn without the app's sidebar; this bar is the screen's whole
  // frame, in every phase, so the gate before recording and the upload after
  // it sit in the same place the transcript does.
  const frame = (body: React.ReactNode) => (
    <div className="flex min-h-screen flex-col bg-[var(--color-surface-panel)]">
      <LiveTopBar meetingId={meetingId} state={recording} elapsedSeconds={live.elapsedSeconds} />
      {body}
    </div>
  );

  if (live.phase === "idle" || live.phase === "error") {
    return frame(
      <main className="max-w-[776px] px-[var(--space-page)] py-[var(--space-24)]">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
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
          {title && (
            <span className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
              {title}
            </span>
          )}
        </div>

        <section className="mt-6" aria-label="입력 장치">
          <SectionLabel>입력 장치</SectionLabel>
          <InputDevice microphone={microphone} />
        </section>

        {notice}
        <label className="mt-6 flex items-start gap-3" style={{ fontSize: "var(--text-body)" }}>
          <input
            type="checkbox"
            checked={consented}
            onChange={(event) => void onConsent(event.target.checked)}
          />
          <span>
            이 회의 참석자 전원이 녹음과 분석에 동의했습니다.
            <span className="block text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
              체크하지 않으면 녹음은 되지만 할 일과 갭은 분석되지 않습니다.
            </span>
          </span>
        </label>
        {consentError && (
          <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
            {consentError}
          </p>
        )}
        {live.error && (
          <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
            {live.error}
          </p>
        )}
        <p className="mt-6 text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
          원본 음성은 처리 후 삭제되고, 전사 텍스트의 개인정보는 저장 전에 자동 마스킹됩니다.
        </p>
        <div className="mt-4 flex items-center justify-end gap-1">
          <Link
            href={`/meetings/new?meeting=${meetingId}`}
            className="inline-flex items-center rounded-[var(--radius)] text-ink-muted focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]"
            style={{
              height: "var(--control-h-default)",
              paddingInline: "var(--control-px-text)",
              fontSize: "var(--control-text-default)",
              fontWeight: "var(--control-weight)",
            }}
          >
            파일 업로드로 대신
          </Link>
          <Button tone="primary" disabled={consentPending} onClick={() => void onStart()}>
            <span className="inline-flex items-center gap-2">
              <span
                aria-hidden
                className="rounded-full"
                style={{
                  width: "var(--space-8)",
                  height: "var(--space-8)",
                  // The label's own colour: white on the accent fill, and the
                  // muted disabled ink while a consent request is in flight.
                  background: "currentColor",
                }}
              />
              녹음 시작
            </span>
          </Button>
        </div>
      </main>,
    );
  }

  if (live.phase === "uploading" || live.phase === "upload_failed") {
    return frame(
      <main className="max-w-[776px] px-[var(--space-page)] py-[var(--space-24)]">
        <p style={{ fontSize: "var(--text-body)" }}>
          {live.phase === "uploading" ? "녹음을 올리는 중입니다…" : "업로드에 실패했습니다."}
        </p>
        {live.phase === "upload_failed" && (
          <UploadFailed
            error={live.error}
            onRetry={() => void live.retryUpload()}
            onSave={live.saveRecording}
          />
        )}
      </main>,
    );
  }

  // Only "connecting" / "recording" / "paused" remain: the session has not
  // reached ready yet, or it is live.
  const state: RecordingState = live.phase === "paused" ? "paused" : "recording";

  // Read on every render, and the level meter re-renders every 100ms, so a
  // track that ends (a headset unplugged) shows up within a tick.
  const track = microphone.stream?.getAudioTracks()[0];
  const microphoneStatus = {
    live: track?.readyState === "live",
    noiseSuppression: track?.getSettings().noiseSuppression === true,
  };

  return frame(
    <>
      {live.liveLost && (
        <p
          role="status"
          className="text-ink-muted"
          style={{ fontSize: "var(--text-meta)", padding: "var(--space-12) var(--space-24) 0" }}
        >
          라이브 전사가 끊겼습니다. 녹음은 계속되고, 정지하면 전체 녹음이 올라갑니다.
        </p>
      )}
      {ending && endingTeamId !== null ? endingSoon?.(endingTeamId) : null}
      <LiveTranscript
        state={state}
        rows={live.rows}
        elapsedSeconds={live.elapsedSeconds}
        levels={microphone.levels}
        microphone={microphoneStatus}
        onPause={live.pause}
        onResume={live.resume}
        onStop={() => void onStop()}
      />
    </>,
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="text-ink-muted"
      style={{
        fontSize: "var(--text-label)",
        fontWeight: "var(--text-label-weight)",
        marginBottom: "var(--space-8)",
      }}
    >
      {children}
    </div>
  );
}

/** How many bars the pre-start meter draws. S10 shows a short meter, not the rail's waveform. */
const METER_BARS = 8;
/** Below this, the last second or so is treated as silence. */
const SILENT = 0.04;

/**
 * S10's input-device row: a level meter, the device picker, and one line of
 * status.
 *
 * The status says only what the meter can tell — whether sound is arriving —
 * and makes no claim about noise suppression or quality. A refused permission
 * replaces the line with red guidance and a "권한 허용" retry: a browser that
 * has blocked the site will not prompt again, so the guidance names where to
 * change it, and the retry is what picks the change up.
 */
function InputDevice({ microphone }: { microphone: Microphone }) {
  const { levels, devices, deviceId, permission, previewing, error } = microphone;
  const recent = levels.slice(-METER_BARS);
  const denied = permission === "denied";
  const hearing = recent.some((level) => level > SILENT);

  let status: React.ReactNode;
  if (denied) {
    status = (
      <span style={{ color: "var(--color-signal-critical)" }}>
        마이크 권한이 거부되어 있습니다. 주소창의 사이트 설정에서 마이크를 허용한 뒤
        &quot;권한 허용&quot;을 눌러 주세요.
      </span>
    );
  } else if (error) {
    status = <span style={{ color: "var(--color-signal-attention)" }}>{error}</span>;
  } else if (!previewing && permission === "prompt") {
    status = "브라우저 주소창 아래에 뜬 창에서 마이크를 허용해 주세요";
  } else if (!previewing) {
    status = "마이크를 여는 중…";
  } else {
    status = hearing ? "입력이 들어오고 있습니다" : "입력이 없습니다. 마이크에 말해 보세요";
  }

  const selectable = devices.length > 0 && permission === "granted";

  return (
    <div
      className="flex items-center gap-4 rounded-[var(--radius)]"
      style={{
        background: "var(--color-surface-sunken)",
        padding: "var(--space-12) var(--space-16)",
      }}
    >
      <div
        role="img"
        aria-label={previewing ? "입력 레벨" : "입력 없음"}
        className="flex flex-none items-end"
        style={{ height: "var(--space-24)", gap: "var(--bar-thickness)" }}
      >
        {recent.map((level, index) => (
          <span
            key={index}
            style={{
              width: "var(--space-4)",
              height: `${Math.max(12, Math.round(level * 100))}%`,
              background: previewing ? meterStep(level) : "var(--color-waveform-q1)",
            }}
          />
        ))}
      </div>
      <div className="min-w-0 flex-1">
        {selectable ? (
          <select
            aria-label="입력 장치"
            value={deviceId}
            onChange={(event) => microphone.selectDevice(event.target.value)}
            className="w-full max-w-full truncate bg-transparent text-ink-strong focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]"
            style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
          >
            {devices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label}
              </option>
            ))}
          </select>
        ) : (
          <div
            className="text-ink-strong"
            style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
          >
            기본 마이크
          </div>
        )}
        <div className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }} aria-live="polite">
          {status}
        </div>
      </div>
      {(denied || (error && !previewing)) && (
        <Button tone="text" size="compact" onClick={() => void microphone.preview()}>
          {denied ? "권한 허용" : "다시 시도"}
        </Button>
      )}
    </div>
  );
}

/** The rail's four achromatic steps: loud is not a warning. */
function meterStep(level: number): string {
  if (level > 0.75) return "var(--color-waveform-q4)";
  if (level > 0.5) return "var(--color-waveform-q3)";
  if (level > 0.25) return "var(--color-waveform-q2)";
  return "var(--color-waveform-q1)";
}
