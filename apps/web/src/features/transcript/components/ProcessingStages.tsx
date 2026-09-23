import Link from "next/link";

import { StatusDot, type StatusVariant } from "@/shared/ui/StatusDot";

import type { MeetingDetail } from "../types";

type StageState = "done" | "running" | "queued" | "failed";

type Stage = { label: string; detail: string; state: StageState };

/**
 * S12, drawn from what the backend actually knows.
 *
 * The design shows six stages with per-stage timings and counts, fed by a
 * WebSocket. That feed does not exist: `process_recording` is one task, and
 * the only things it writes as it goes are the meeting's status and, at the
 * end, two flags. So this component derives each stage from those and says
 * nothing it cannot back:
 *
 * - **upload** is done — the page exists because the upload returned 202.
 * - **STT / diarization** run while the meeting is `analyzing`; there is no
 *   way to tell which of the two is in progress, so both are "진행".
 * - **PII masking** and **deleting the original** are read from their flags,
 *   which `persist_transcript` sets in the same transaction as the
 *   utterances. Until then masking shows as running (it happens inside the
 *   task) and deletion as queued (it happens last).
 * - **B · C · D** start when `TranscriptReady` goes out, which is after
 *   `complete`. Module A cannot see their progress and this feature may not
 *   ask them (`CLAUDE.md`), so it says "시작됨" and points at their tabs.
 *
 * `failed` turns the running stage red. The task does not report which step
 * raised, so the reason is generic and the retry is a new upload for the
 * same meeting — the pipeline accepts a recording for a `failed` meeting.
 */
export function ProcessingStages({ meeting }: { meeting: MeetingDetail }) {
  const stages = stagesFor(meeting);

  return (
    <section aria-label="처리 단계">
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
              {STATE_LABEL[stage.state]}
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
            : "이 페이지를 떠나도 처리는 계속됩니다. 단계별 진행률은 아직 제공되지 않아 회의 상태로 표시합니다."}
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

function stagesFor(meeting: MeetingDetail): Stage[] {
  const recording = meeting.status === "recording";
  const analyzing = meeting.status === "analyzing";
  const failed = meeting.status === "failed";
  // `recording` is the live channel's status (#307): the browser still holds
  // the audio and nothing has run on the server yet.
  const finished =
    !recording && !analyzing && !failed && meeting.status !== "scheduled";

  // While analyzing the task is somewhere between decode and the final write;
  // the first stage that is not yet backed by a flag is the one that shows red
  // on failure. While recording, nothing has started.
  const recognition: StageState = finished
    ? "done"
    : failed
      ? "failed"
      : recording
        ? "queued"
        : "running";
  const masking: StageState = meeting.pii_masked
    ? "done"
    : failed || recording
      ? "queued"
      : "running";
  const deletion: StageState = meeting.original_audio_deleted
    ? "done"
    : failed
      ? "done" // adopt() deletes in a finally; a failed task has no recording left
      : "queued";

  return [
    {
      label: "업로드 · 형식 검증",
      detail: recording
        ? "녹음이 끝나면 브라우저가 올립니다"
        : "서버가 받았고, ffmpeg 가 16kHz mono 로 변환합니다",
      state: recording ? "queued" : "done",
    },
    {
      label: "음성 인식",
      detail: "Whisper large-v3 · 한국어 · 회의 용어집 적용",
      state: recognition,
    },
    {
      label: "화자 분리",
      detail: "pyannote · 화자 식별은 아직 없어 SPEAKER_00, 01 … 로 표시됩니다",
      state: recognition,
    },
    {
      label: "개인정보 마스킹",
      detail: "전화 · 이메일 · 주민번호 · 계좌 · 카드 — 저장 전에 마스킹",
      state: masking,
    },
    {
      label: "원본 음성 삭제",
      detail: "전사가 끝나면 즉시 삭제되고, 삭제 여부가 회의에 기록됩니다",
      state: deletion,
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
