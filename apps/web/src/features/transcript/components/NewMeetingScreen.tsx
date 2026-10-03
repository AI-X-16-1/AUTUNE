"use client";

import Link from "next/link";
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

type Source = "live" | "file";
/** Matches `MAX_UPLOAD_BYTES` in `modules/audio/src/autune_audio/config.py` and S03's dropzone. */
const MAX_BYTES = 500 * 1024 * 1024;

/**
 * `now` as local wall time, `YYYY-MM-DDTHH:MM` — split at the `T` into what
 * `<input type="date">` and `<input type="time">` want.
 *
 * `toISOString()` would be UTC, which the inputs render as a time the person
 * did not mean. Subtracting the offset first makes the slice come out as what
 * their own clock reads.
 */
function localNow(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

/**
 * What the browser typed, as an instant the API can store.
 *
 * The date and time inputs have no zone, so `new Date("<date>T<time>")` reads
 * them in the browser's own — which is the intent: somebody typing "14:00"
 * means two in the afternoon where they are. Same payload as the single
 * `datetime-local` input this replaced.
 */
function asInstant(date: string, time: string): string | undefined {
  if (!date || !time) return undefined;
  const parsed = new Date(`${date}T${time}`);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

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
 * Not the whole of S06. Attendee chips and Notion and Jira overrides are
 * drawn in the spec and depend on things that do not exist yet
 * (identification #6, integrations per meeting). What is here is what
 * makes a recording reach the pipeline from a browser instead of from `curl`.
 *
 * A meeting can also be opened with no recording at all — "저장만", beside
 * the primary button, under either source. It stops after the first call and
 * leaves the meeting `scheduled`, which is what D's pre-meeting brief waits on: a
 * `scheduled` meeting whose `started_at` is ahead gets a brief ten minutes
 * before it (#437, #469). The upload path cannot produce one, because it moves
 * the meeting to `analyzing` in the same submit. The recording arrives later
 * through the stored meeting's "녹음 파일 올리기", which comes back here with
 * `?meeting=`. One form rather than two screens: a meeting that already
 * happened and one that is about to are the same title, time and team, and the
 * only difference is whether there is a file yet. Consent is not asked on this
 * path — there is no recording to attest to, and the upload asks for it.
 *
 * **Two sources.** "웹 마이크 실시간" opens the meeting and goes straight to
 * S13 (`/meetings/<id>/live`), which checks the microphone, asks for consent
 * and records; "녹음 파일 업로드" is the upload form below. S06 draws the
 * same choice as its audio-source radio. Before it, the only way to record
 * live was to schedule a meeting, open it, and press record from there. Live
 * is the default: "회의 시작" in the sidebar means a meeting starting now.
 * The date and start fields are read by the upload and by "저장만"; "지금
 * 녹음 시작" ignores them — a live meeting starts when it is opened.
 *
 * Field order follows S06: title, date and start, team, audio source, then
 * the Phase 2 rows drawn disabled. S06's attendee chips and its optional end
 * time are omitted: neither has a field in the create payload.
 *
 * `?meeting=` uploads to an existing meeting — the retry S12 offers when a
 * run failed, and S10's "파일 업로드로 대신" for a meeting opened to record
 * live. The backend accepts a recording for a `scheduled`, `recording` or
 * `failed` meeting and refuses one for a meeting that is `analyzing` or
 * `complete`, and its 409 is shown as-is.
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
  // When the meeting happened, not when the file is being uploaded. It
  // defaults to now because most uploads follow the meeting closely, and it is
  // editable because a recording carried over from yesterday is the case that
  // makes the default wrong (#340).
  const [date, setDate] = useState(() => localNow().slice(0, 10));
  const [time, setTime] = useState(() => localNow().slice(11, 16));
  // A re-upload is always a file; a new meeting defaults to recording live.
  const [source, setSource] = useState<Source>(
    existingMeetingId ? "file" : "live",
  );
  const [file, setFile] = useState<File | null>(null);
  const [consented, setConsented] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<
    "idle" | "creating" | "consenting" | "uploading" | "scheduling" | "opening"
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
  // No file, no consent: only what the meeting row itself needs. The start is
  // required here, unlike on the upload path's API call, because a scheduled
  // meeting with no time is one the brief can never be sent for.
  const canSchedule =
    existingMeetingId === undefined &&
    title.trim().length > 0 &&
    teamId !== "" &&
    asInstant(date, time) !== undefined &&
    step === "idle";

  const canGoLive =
    existingMeetingId === undefined &&
    title.trim().length > 0 &&
    teamId !== "" &&
    step === "idle";

  /** Open the meeting now and hand over to S13, which asks for consent. */
  async function goLive() {
    if (!canGoLive) return;
    setError(null);
    try {
      setStep("opening");
      const { meeting_id } = await createMeeting({
        title: title.trim(),
        team_id: teamId,
        started_at: new Date().toISOString(),
      });
      router.push(`/meetings/${meeting_id}/live`);
    } catch (e: unknown) {
      setStep("idle");
      setError(e instanceof Error ? e.message : "회의를 만들지 못했습니다");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (source === "live") {
      await goLive();
      return;
    }
    if (!ready || !file) return;
    setError(null);
    try {
      let meetingId = existingMeetingId;
      if (!meetingId) {
        setStep("creating");
        meetingId = (
          await createMeeting({
            title: title.trim(),
            team_id: teamId,
            started_at: asInstant(date, time),
          })
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

  async function schedule() {
    if (!canSchedule) return;
    setError(null);
    try {
      setStep("scheduling");
      const { meeting_id } = await createMeeting({
        title: title.trim(),
        team_id: teamId,
        started_at: asInstant(date, time),
      });
      router.push(`/meetings/${meeting_id}`);
    } catch (e: unknown) {
      setStep("idle");
      setError(e instanceof Error ? e.message : "회의를 만들지 못했습니다");
    }
  }

  return (
    <main className="max-w-[776px] px-[var(--space-page)] py-[var(--space-24)]">
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
            : source === "live"
              ? "마이크로 녹음하면서 바로 전사합니다. 녹음을 끝내면 전체 녹음이 올라가 분석되고, 원본은 처리 후 삭제됩니다."
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
            <div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="날짜">
                  <input
                    type="date"
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                    className={INPUT}
                    style={{ ...INPUT_STYLE, fontFamily: "var(--font-mono)" }}
                  />
                </Field>
                <Field label="시작">
                  <input
                    type="time"
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                    className={INPUT}
                    style={{ ...INPUT_STYLE, fontFamily: "var(--font-mono)" }}
                  />
                </Field>
              </div>
              <p
                className="mt-1 text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {source === "live"
                  ? "“지금 녹음 시작”은 지금 시각으로 열립니다. 앞으로 열 회의는 그 시각으로 “저장만”을 누르면 시작 10분 전에 브리프가 옵니다."
                  : "“이번 주 금요일까지” 같은 표현을 언제 기준으로 읽을지 정합니다. 지난 회의 녹음이면 그때로 고쳐 주세요."}
              </p>
            </div>
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
                  속한 팀이 없습니다.{" "}
                  <Link href="/workspace/new" className="text-[var(--color-accent-default)]">
                    워크스페이스 만들기
                  </Link>
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
              {teams !== null && teams.length > 0 ? (
                <Link
                  href="/workspace/new"
                  className="ml-3 text-[var(--color-accent-default)]"
                  style={{ fontSize: "var(--text-meta)" }}
                >
                  새 팀 만들기
                </Link>
              ) : null}
            </Field>
            <Field label="오디오 소스">
              <SourceChoice value={source} onChange={setSource} />
            </Field>
          </>
        )}

        {source === "file" && (
          <>
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
                    : "mp3 · wav · m4a · 최대 500MB"}
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
                이 녹음에 포함된 모든 참석자가 녹음과 분석에 동의했음을
                확인합니다.
                <span
                  className="block text-[var(--color-ink-muted)]"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  동의가 기록되지 않은 회의는 전사만 저장되고 액션 · 갭 분석에서
                  제외됩니다.
                </span>
              </span>
            </label>
          </>
        )}

        {existingMeetingId ? null : <LaterOptions />}

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

        <div className="flex items-center gap-1">
          <span
            className="flex-1 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            {source === "live"
              ? "다음 화면에서 마이크와 참석자 동의를 확인한 뒤 녹음을 시작합니다."
              : "업로드 즉시 처리가 시작되고, 원본은 처리 후 삭제됩니다."}
          </span>
          {existingMeetingId ? null : (
            <Button
              tone="quiet"
              type="button"
              onClick={schedule}
              disabled={!canSchedule}
              loading={step === "scheduling"}
            >
              {step === "scheduling" ? "저장 중…" : "저장만"}
            </Button>
          )}
          {source === "live" ? (
            <Button
              tone="primary"
              type="submit"
              disabled={!canGoLive}
              loading={step === "opening"}
            >
              {step === "opening" ? (
                "회의 만드는 중…"
              ) : (
                <span className="inline-flex items-center gap-2">
                  <LeadingDot />
                  지금 녹음 시작
                </span>
              )}
            </Button>
          ) : (
            <Button
              tone="primary"
              type="submit"
              disabled={!ready || step === "scheduling"}
              loading={step !== "idle" && step !== "scheduling"}
            >
              {STEP_LABEL[step === "scheduling" ? "idle" : step]}
            </Button>
          )}
        </div>
      </form>
    </main>
  );
}

/**
 * The white dot S06 and S10 put before "녹음 시작". It takes the label's own
 * colour, so a disabled button greys it with the text.
 */
function LeadingDot() {
  return (
    <span
      aria-hidden
      className="rounded-full"
      style={{
        width: "var(--space-8)",
        height: "var(--space-8)",
        background: "currentColor",
      }}
    />
  );
}

/**
 * S06's audio-source choice: two options with a radio circle, the first
 * decision that changes the rest of the form. Selected is a 1.5px accent
 * border and a filled circle, with no background fill, as S06 draws it.
 *
 * The live option's detail names only what the live path does today:
 * transcription as it happens, and the same full analysis once the recording
 * is uploaded at stop. S06's "중간 요약 · 자료 감지" are Phase 2 and not
 * claimed here.
 */
function SourceChoice({
  value,
  onChange,
}: {
  value: Source;
  onChange: (source: Source) => void;
}) {
  const options: { id: Source; title: string; detail: string }[] = [
    {
      id: "live",
      title: "웹 마이크 실시간",
      detail: "실시간 전사 · 종료 후 전체 분석",
    },
    {
      id: "file",
      title: "녹음 파일 업로드",
      detail: "회의 후 업로드 · 동일 분석",
    },
  ];
  return (
    <div
      role="radiogroup"
      aria-label="오디오 소스"
      className="grid grid-cols-2 gap-3"
    >
      {options.map((option) => {
        const selected = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.id)}
            className="flex items-start gap-3 rounded-[var(--radius)] bg-[var(--color-surface-panel)] text-left focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]"
            style={{
              padding: "var(--space-12)",
              border: selected ? "var(--border-focus)" : "var(--border-input)",
            }}
          >
            <span
              aria-hidden
              className="mt-[1px] grid flex-none place-items-center rounded-full"
              style={{
                width: "var(--space-16)",
                height: "var(--space-16)",
                border: selected ? "var(--border-focus)" : "var(--border-input)",
              }}
            >
              {selected ? (
                <span
                  className="rounded-full"
                  style={{
                    width: "var(--dot-size)",
                    height: "var(--dot-size)",
                    background: "var(--color-accent-default)",
                  }}
                />
              ) : null}
            </span>
            <span>
              <span
                className="block text-[var(--color-ink-strong)]"
                style={{
                  fontSize: "var(--text-rowTitle)",
                  fontWeight: "var(--text-rowTitle-weight)",
                }}
              >
                {option.title}
              </span>
              <span
                className="mt-[2px] block text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-meta)" }}
              >
                {option.detail}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * S06's Phase 2 rows, drawn disabled rather than hidden so the roadmap shows.
 *
 * S06 also draws Notion and Jira rows here as per-meeting overrides. Those
 * are left out, not drawn disabled: the integrations exist at team level, and
 * a per-meeting override row would read as a setting this form saves when it
 * does not.
 */
function LaterOptions() {
  const rows = [
    { title: "종료 5분 전 미결정 사항 알림", detail: "종료 시간 기준 · Phase 2" },
    { title: "자료 연결 후 어젠다 자동 생성", detail: "PRD · 이전 회의록 · Phase 2" },
  ];
  return (
    <div className="flex flex-col border-t border-[var(--color-hairline)]">
      {rows.map((row) => (
        <label
          key={row.title}
          className="flex cursor-not-allowed items-center gap-3 border-b border-[var(--color-hairline)]"
          style={{ paddingBlock: "var(--space-12)" }}
        >
          <input type="checkbox" disabled checked={false} readOnly />
          <span>
            <span
              className="block text-[var(--color-ink-muted)]"
              style={{
                fontSize: "var(--text-rowLabel)",
                fontWeight: "var(--text-rowLabel-weight)",
              }}
            >
              {row.title}
            </span>
            <span
              className="block text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-meta)" }}
            >
              {row.detail}
            </span>
          </span>
        </label>
      ))}
    </div>
  );
}

const STEP_LABEL = {
  idle: "업로드하고 분석 시작",
  creating: "회의 만드는 중…",
  consenting: "동의 기록 중…",
  uploading: "업로드 중…",
  opening: "회의 만드는 중…",
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
        className="text-[var(--color-ink-body)]"
        style={{
          fontSize: "var(--text-label)",
          fontWeight: "var(--text-label-weight)",
          marginBottom: "var(--space-8)",
        }}
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
