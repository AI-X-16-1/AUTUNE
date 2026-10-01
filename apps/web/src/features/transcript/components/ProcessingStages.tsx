import Link from "next/link";

import { StatusDot, type StatusVariant } from "@/shared/ui/StatusDot";

import type { MeetingDetail, ProcessingStage } from "../types";

type StageState = "done" | "running" | "queued" | "failed";

type Stage = { label: string; detail: string; state: StageState; progress?: number };

/**
 * S12, drawn from where the worker says it is.
 *
 * The worker records its current step and how far through it is on the job
 * row (`autune_audio.progress`), and `GET /meetings/{id}` returns both, so this
 * shows the step that is actually running, a percentage for it, and an
 * overall percentage above the list. Before this, the task wrote only the
 * meeting's status, and recognition, diarization and masking all read "진행"
 * for the whole of a transcription that takes about as long as the meeting.
 *
 * - **The rows are in the order the worker runs them.** The original is
 *   deleted when diarization ends (`storage.adopt` closes there), before
 *   masking — so deletion is listed above masking, not below as the design
 *   file has it. The screen says what happened in the order it happened.
 * - **The overall figure is weighted, not measured.** Recognition dominates
 *   (Whisper at about real time on CPU), diarization is most of the rest, and
 *   decoding, masking and saving are seconds. `WEIGHT` says so, and the label
 *   says "약".
 * - **B · C · D** start when `TranscriptReady` goes out, which is after
 *   `complete`. Module A cannot see their progress and this feature may not
 *   ask them (`CLAUDE.md`), so it says "시작됨" and points at their tabs.
 *
 * `failed` turns the step that was running red. The retry is a new upload
 * for the same meeting — the pipeline accepts a recording for a `failed`
 * meeting.
 */
export function ProcessingStages({ meeting }: { meeting: MeetingDetail }) {
  const stages = stagesFor(meeting);
  const overall = overallProgress(meeting);

  return (
    <section aria-label="처리 단계">
      {overall !== null && (
        <Overall value={overall} waiting={meeting.stage === null} />
      )}
      <ol className="border-t border-[var(--color-hairline)]">
        {stages.map((stage) => (
          <li
            key={stage.label}
            className="flex items-start gap-3 border-b border-[var(--color-hairline)]"
            style={{ paddingBlock: "var(--space-row)" }}
          >
            <StatusDot
              variant={VARIANT[stage.state]}
              hollow={stage.state === "queued"}
              className="mt-[7px]"
            />
            <div className="min-w-0 flex-1">
              <div
                className="text-[var(--color-ink-strong)]"
                style={{
                  fontSize: "var(--text-rowTitle)",
                  fontWeight: "var(--text-rowTitle-weight)",
                }}
              >
                {stage.label}
              </div>
              <div
                className="text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {stage.detail}
              </div>
              {stage.state === "running" && stage.progress !== undefined && (
                <Bar value={stage.progress} label={`${stage.label} 진행률`} />
              )}
            </div>
            <span
              className="shrink-0"
              style={{
                fontSize: "var(--text-metaSmall)",
                color:
                  stage.state === "failed"
                    ? "var(--color-signal-critical)"
                    : "var(--color-ink-muted)",
              }}
            >
              {stage.state === "running" && stage.progress !== undefined ? (
                <span
                  className="tabular-nums text-[var(--color-accent-default)]"
                  style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-data)", fontWeight: "var(--text-data-weight)" }}
                >
                  {percent(stage.progress)}
                </span>
              ) : (
                STATE_LABEL[stage.state]
              )}
            </span>
          </li>
        ))}
      </ol>

      {meeting.status === "failed" ? (
        <p
          role="alert"
          className="mt-3"
          style={{ fontSize: "var(--text-meta)" }}
        >
          <span style={{ color: "var(--color-signal-critical)" }}>
            처리에 실패했습니다. 원본 녹음은 삭제되었습니다.
          </span>{" "}
          <Link
            href={`/meetings/new?meeting=${meeting.meeting_id}`}
            className="text-[var(--color-accent-default)]"
          >
            다시 업로드
          </Link>
        </p>
      ) : (
        <p
          className="mt-3 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          {meeting.status === "recording"
            ? "녹음 중인 브라우저가 올리면 처리가 시작됩니다."
            : "이 페이지를 떠나도 처리는 계속됩니다."}
        </p>
      )}
    </section>
  );
}

const VARIANT: Record<StageState, StatusVariant> = {
  done: "confirmed",
  running: "progress",
  queued: "idle",
  failed: "critical",
};

const STATE_LABEL: Record<StageState, string> = {
  done: "완료",
  running: "진행",
  queued: "대기",
  failed: "실패",
};

/** In the order `process_recording` runs them. */
const ORDER: ProcessingStage[] = ["decoding", "transcribing", "diarizing", "masking", "saving"];

/**
 * Each step's share of the overall figure. Whisper at roughly real time
 * dominates; pyannote's embedding pass is most of the rest; the other three
 * take seconds. Estimates, which is why the overall label says "약".
 */
const WEIGHT: Record<ProcessingStage, number> = {
  decoding: 0.03,
  transcribing: 0.62,
  diarizing: 0.3,
  masking: 0.03,
  saving: 0.02,
};

/** Where the running step sits in ORDER; -1 before the worker starts. */
function position(meeting: MeetingDetail): number {
  return meeting.stage === null ? -1 : ORDER.indexOf(meeting.stage);
}

/** 0..1 across the whole task, or null when there is nothing to measure. */
function overallProgress(meeting: MeetingDetail): number | null {
  if (meeting.status !== "analyzing") return null;
  const at = position(meeting);
  if (at < 0) return 0;
  let total = 0;
  for (const [index, stage] of ORDER.entries()) {
    if (index < at) total += WEIGHT[stage];
    else if (index === at) total += WEIGHT[stage] * (meeting.stage_progress ?? 0);
  }
  return Math.min(1, total);
}

function stagesFor(meeting: MeetingDetail): Stage[] {
  const recording = meeting.status === "recording";
  const analyzing = meeting.status === "analyzing";
  const failed = meeting.status === "failed";
  // `recording` is the live channel's status (#307): the browser still holds
  // the audio and nothing has run on the server yet.
  const finished = !recording && !analyzing && !failed && meeting.status !== "scheduled";
  const at = position(meeting);
  const fraction = meeting.stage_progress ?? 0;

  /** One step's state from where the worker is. `upTo` is the last ORDER
   * index the row covers, so masking and saving share a row. */
  const state = (from: number, upTo: number = from): StageState => {
    if (finished) return "done";
    if (recording || meeting.status === "scheduled") return "queued";
    if (at > upTo) return "done";
    if (at >= from) return failed ? "failed" : "running";
    return "queued";
  };
  const progress = (from: number, upTo: number = from): number | undefined => {
    if (at < from || at > upTo || !analyzing) return undefined;
    // A shared row counts its steps as one: masking is its first half.
    const span = upTo - from + 1;
    return (at - from + fraction) / span;
  };

  const deleted =
    meeting.original_audio_deleted || finished || failed || at >= ORDER.indexOf("masking");

  return [
    {
      label: "업로드 · 형식 검증",
      detail: recording
        ? "녹음이 끝나면 브라우저가 올립니다"
        : "서버가 받았고, ffmpeg 가 16kHz mono 로 변환합니다",
      state: recording ? "queued" : at === 0 && !failed ? "running" : at === 0 ? "failed" : "done",
    },
    {
      label: "음성 인식",
      detail: "Whisper large-v3 · 한국어 · 회의 용어집 적용",
      state: state(1),
      progress: progress(1),
    },
    {
      label: "화자 분리",
      detail: "pyannote · 화자 식별은 아직 없어 화자 1, 2 … 로 표시됩니다",
      state: state(2),
      progress: progress(2),
    },
    {
      label: "원본 음성 삭제",
      detail: "화자 분리가 끝나면 바로 삭제되고, 삭제 여부가 회의에 기록됩니다",
      state: deleted ? "done" : "queued",
    },
    {
      label: "개인정보 마스킹 · 저장",
      detail: "전화 · 이메일 · 주민번호 · 계좌 · 카드 — 저장 전에 마스킹",
      state: meeting.pii_masked ? "done" : state(3, 4),
      progress: progress(3, 4),
    },
    {
      label: "구조화 · 갭 · 맥락 분석",
      detail: finished
        ? "B · C · D 에 전달됨 — 결과는 각 탭에서 확인합니다"
        : "전사가 끝나면 B · C · D 가 병렬로 시작합니다",
      state: finished ? "done" : "queued",
    },
  ];
}

function percent(value: number): string {
  return `${Math.floor(value * 100)}%`;
}

/** P3 in ui-spec: a 2px bar, accent while running. */
function Bar({ value, label }: { value: number; label: string }) {
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.floor(value * 100)}
      className="mt-2"
      style={{ height: "var(--bar-thickness)", background: "var(--color-surface-sunken)" }}
    >
      <div
        style={{
          width: `${Math.min(100, value * 100)}%`,
          height: "100%",
          background: "var(--color-accent-default)",
          transition: "width 600ms ease-out",
        }}
      />
    </div>
  );
}

/** The whole task at a glance, above the steps. */
function Overall({ value, waiting }: { value: number; waiting: boolean }) {
  return (
    <div style={{ marginBottom: "var(--space-16)" }}>
      <div className="flex items-baseline justify-between">
        <span
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
        >
          {waiting ? "처리를 시작하는 중입니다" : "전사하는 중입니다"}
        </span>
        <span
          className="tabular-nums text-[var(--color-ink-strong)]"
          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-heading)", fontWeight: 600 }}
        >
          약 {percent(value)}
        </span>
      </div>
      <Bar value={value} label="전체 진행률" />
    </div>
  );
}
