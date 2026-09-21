"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/shared/ui/Button";

import {
  attestConsent,
  createMeeting,
  listTeams,
  uploadRecording,
} from "../api";
import type { TeamSummary } from "../types";

const ACCEPTED = [".mp3", ".wav", ".m4a"];
/** Matches `MAX_UPLOAD_BYTES` in `modules/audio/src/autune_audio/config.py` and S03's dropzone. */
const MAX_BYTES = 500 * 1024 * 1024;

/**
 * S06's file-upload path, with S03's dropzone and S10's consent, on one page.
 *
 * Three calls in the order the backend needs them: open the meeting, attest
 * consent, upload. Consent goes **before** the upload on purpose — B and C
 * analyse only consented utterances, and a transcript written before the
 * attestation is one they analyse nothing of (#190, #283). The checkbox is the
 * attestation: unchecked, the button stays disabled, and there is no way to
 * upload without making the statement.
 *
 * Not the whole of S06. Attendee chips, the web-microphone source, Notion and
 * Jira overrides are drawn in the spec and depend on things that do not exist
 * yet (identification #6, integrations per meeting). What is here is what
 * makes a recording reach the pipeline from a browser instead of from `curl`.
 *
 * `?meeting=` re-uploads to an existing meeting — the retry S12 offers when a
 * run failed. The backend accepts a recording for a `failed` meeting and
 * refuses one for a meeting that is `analyzing` or `complete`, and its 409 is
 * shown as-is.
 *
 * Validation before the request: extension and size, because a 500MB body
 * that the server then refuses is 500MB of somebody's time. The server checks
 * both again; ffmpeg decides what the bytes actually are.
 */
export function NewMeetingScreen({
  existingMeetingId,
}: {
  existingMeetingId?: string;
}) {
  const router = useRouter();
  const [teams, setTeams] = useState<TeamSummary[] | null>(null);
  const [teamId, setTeamId] = useState("");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [consented, setConsented] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<
    "idle" | "creating" | "consenting" | "uploading"
  >("idle");

  useEffect(() => {
    if (existingMeetingId) return;
    let current = true;
    listTeams()
      .then((list) => {
        if (!current) return;
        setTeams(list);
        const first = list[0];
        if (first) setTeamId(first.team_id);
      })
      .catch((e: unknown) => {
        if (current)
          setError(
            e instanceof Error ? e.message : "팀 목록을 불러오지 못했습니다",
          );
      });
    return () => {
      current = false;
    };
  }, [existingMeetingId]);

  const fileProblem = file ? validate(file) : null;
  const ready =
    file !== null &&
    fileProblem === null &&
    consented &&
    (existingMeetingId !== undefined ||
      (title.trim().length > 0 && teamId !== ""));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || !file) return;
    setError(null);
    try {
      let meetingId = existingMeetingId;
      if (!meetingId) {
        setStep("creating");
        meetingId = (
          await createMeeting({ title: title.trim(), team_id: teamId })
        ).meeting_id;
      }
      setStep("consenting");
      await attestConsent(meetingId);
      setStep("uploading");
      await uploadRecording(meetingId, file);
      router.push(`/meetings/${meetingId}`);
    } catch (e: unknown) {
      setStep("idle");
      setError(e instanceof Error ? e.message : "업로드에 실패했습니다");
    }
  }

  return (
    <main className="mx-auto max-w-[720px] p-[var(--space-page)]">
      <header>
        <h1
          className="text-ink-strong"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          {existingMeetingId ? "녹음 다시 올리기" : "회의 만들기"}
        </h1>
        <p
          className="mt-2 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          {existingMeetingId
            ? existingMeetingId
            : "녹음 파일을 올리면 STT → 화자 분리 → 개인정보 마스킹 순으로 처리되고, 원본은 처리 후 삭제됩니다."}
        </p>
      </header>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-5">
        {existingMeetingId ? null : (
          <>
            <Field label="제목">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={400}
                placeholder="검색 개인화 기능 스프린트 킥오프"
                className={INPUT}
                style={INPUT_STYLE}
              />
            </Field>
            <Field label="팀">
              {teams === null ? (
                <span
                  className="text-[var(--color-ink-muted)]"
                  style={{ fontSize: "var(--text-meta)" }}
                >
                  불러오는 중…
                </span>
              ) : teams.length === 0 ? (
                <span
                  style={{
                    fontSize: "var(--text-meta)",
                    color: "var(--color-signal-attention)",
                  }}
                >
                  속한 팀이 없습니다. 토큰을 확인해 주세요.
                </span>
              ) : (
                <select
                  value={teamId}
                  onChange={(e) => setTeamId(e.target.value)}
                  className={INPUT}
                  style={INPUT_STYLE}
                >
                  {teams.map((team) => (
                    <option key={team.team_id} value={team.team_id}>
                      {team.name}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </>
        )}

        <Field label="녹음 파일">
          <label
            className="flex cursor-pointer flex-col items-center justify-center gap-1 rounded-[var(--radius)] border border-dashed border-[var(--color-hairline)] px-4 py-8 text-center"
            style={{ background: "var(--color-surface-sunken)" }}
          >
            <input
              type="file"
              accept={ACCEPTED.join(",")}
              className="sr-only"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <span
              className="text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-rowTitle)",
                fontWeight: "var(--text-rowTitle-weight)",
              }}
            >
              {file ? file.name : "녹음 파일 선택"}
            </span>
            <span
              className="text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              {file
                ? formatBytes(file.size)
                : "mp3 · wav · m4a · 최대 3h · 500MB"}
            </span>
          </label>
          {fileProblem ? (
            <p
              role="alert"
              className="mt-2"
              style={{
                fontSize: "var(--text-metaSmall)",
                color: "var(--color-signal-critical)",
              }}
            >
              {fileProblem}
            </p>
          ) : null}
        </Field>

        <label
          className="flex items-start gap-3"
          style={{ fontSize: "var(--text-meta)" }}
        >
          <input
            type="checkbox"
            checked={consented}
            onChange={(e) => setConsented(e.target.checked)}
            className="mt-[3px]"
          />
          <span className="text-[var(--color-ink-strong)]">
            이 녹음에 포함된 모든 참석자가 녹음과 분석에 동의했음을 확인합니다.
            <span
              className="block text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              동의가 기록되지 않은 회의는 전사만 저장되고 액션 · 갭 분석에서
              제외됩니다.
            </span>
          </span>
        </label>

        {error ? (
          <p
            role="alert"
            style={{
              fontSize: "var(--text-meta)",
              color: "var(--color-signal-critical)",
            }}
          >
            {error}
          </p>
        ) : null}

        <div className="flex items-center gap-3">
          <Button
            tone="primary"
            type="submit"
            disabled={!ready}
            loading={step !== "idle"}
          >
            {STEP_LABEL[step]}
          </Button>
          <span
            className="text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            업로드 즉시 처리가 시작되고, 원본은 처리 후 삭제됩니다.
          </span>
        </div>
      </form>
    </main>
  );
}

const STEP_LABEL = {
  idle: "업로드하고 분석 시작",
  creating: "회의 만드는 중…",
  consenting: "동의 기록 중…",
  uploading: "업로드 중…",
} as const;

const INPUT =
  "w-full rounded-[var(--radius)] border border-[var(--color-hairline)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";
const INPUT_STYLE = {
  height: "var(--control-h-default)",
  fontSize: "var(--control-text-default)",
};

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div
        className="mb-1 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function validate(file: File): string | null {
  const dot = file.name.lastIndexOf(".");
  const ext = dot === -1 ? "" : file.name.slice(dot).toLowerCase();
  if (!ACCEPTED.includes(ext)) return "mp3, wav, m4a 파일만 올릴 수 있습니다.";
  if (file.size > MAX_BYTES) return "500MB 를 넘는 파일은 올릴 수 없습니다.";
  return null;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)}KB`;
  return `${bytes}B`;
}
