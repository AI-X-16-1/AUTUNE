"use client";

import { Button } from "@/shared/ui";

import { timecode } from "../format";
import { KIND_LABELS, type RecordingState, type UtteranceKind } from "../types";

/**
 * The right rail: how long this has been running, what it has found, and the
 * two controls that change the recording.
 *
 * The timer is 56px and monospace because it is the one number somebody glances
 * at from across a table. Red belongs to it — elapsing time is what red means
 * here, and it is why the stop button is not red: red is the state, not the
 * action.
 *
 * Counts are per kind and per meeting. **There is no per-person count, here or
 * anywhere**, and adding one would turn the rail into a scoreboard of who
 * talked — `privacy.md` section 3.
 */

export function LiveRail({
  state,
  elapsedSeconds,
  plannedSeconds,
  levels,
  counts,
  onPause,
  onResume,
  onStop,
}: {
  state: RecordingState;
  elapsedSeconds: number;
  /** From the meeting's scheduled end. Undefined when nobody set one. */
  plannedSeconds?: number;
  /** Recent input levels, 0..1, oldest first. */
  levels: number[];
  counts?: Partial<Record<UtteranceKind, number>>;
  /** Undefined until module B has reported.
   *
   * B analyses a finished meeting: `TranscriptReady` is published once, when
   * recording stops, and there is no incremental path into B on purpose
   * (`audio.md`). So every kind is unknown for the whole recording, and
   * rendering `counts[kind] ?? 0` turned "we have not looked" into "we looked
   * and found none" — `확인 필요 0` reads as a claim that nothing ambiguous was
   * said. This screen says what it knows and nothing else. */
  onPause?: () => void;
  onResume?: () => void;
  onStop?: () => void;
}) {
  const progress =
    plannedSeconds && plannedSeconds > 0
      ? Math.min(1, elapsedSeconds / plannedSeconds)
      : undefined;

  return (
    <aside
      className="flex flex-col gap-6"
      aria-label="녹음"
      style={{ width: 240 }}
    >
      <div className="flex items-center gap-2">
        {state === "recording" ? (
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--color-signal-critical)",
            }}
          />
        ) : null}
        <span
          className="tabular-nums"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 56,
            lineHeight: 1,
            color:
              state === "recording"
                ? "var(--color-signal-critical)"
                : "var(--color-ink-muted)",
          }}
        >
          {timecode(elapsedSeconds)}
        </span>
      </div>

      {progress === undefined ? null : (
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress * 100)}
          aria-label="예정 시간 대비 경과"
          style={{
            height: 2,
            background: "var(--color-surface-sunken)",
            borderRadius: "var(--radius)",
          }}
        >
          <div
            style={{
              width: `${progress * 100}%`,
              height: "100%",
              background: "var(--color-ink-muted)",
              borderRadius: "var(--radius)",
            }}
          />
        </div>
      )}

      <Waveform levels={levels} live={state === "recording"} />

      {counts === undefined ? (
        <p
          style={{
            fontSize: "var(--text-status)",
            color: "var(--color-ink-muted)",
          }}
        >
          회의가 끝나면 분류됩니다
        </p>
      ) : (
        <dl className="flex flex-col gap-1">
          {(Object.keys(KIND_LABELS) as UtteranceKind[]).map((kind) => (
            <div key={kind} className="flex items-baseline justify-between">
              <dt
                style={{
                  fontSize: "var(--text-status)",
                  color: "var(--color-ink-muted)",
                }}
              >
                {KIND_LABELS[kind]}
              </dt>
              <dd
                className="tabular-nums"
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-data)",
                  color: "var(--color-ink-strong)",
                }}
              >
                {counts[kind] ?? 0}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <div className="flex items-center gap-2">
        {state === "recording" ? (
          <Button tone="secondary" size="compact" onClick={onPause}>
            일시정지
          </Button>
        ) : null}
        {state === "paused" ? (
          <Button tone="secondary" size="compact" onClick={onResume}>
            이어서 녹음
          </Button>
        ) : null}
        {state === "ended" ? null : (
          <Button tone="primary" size="compact" onClick={onStop}>
            녹음 종료
          </Button>
        )}
      </div>
    </aside>
  );
}

/**
 * Input level over the last few seconds.
 *
 * Four achromatic steps, the same ramp charts use: a waveform that changes
 * colour with volume reads as a warning about something. Loud is not a problem.
 */
function Waveform({ levels, live }: { levels: number[]; live: boolean }) {
  const step = (level: number) =>
    level > 0.75
      ? "var(--color-waveform-q4)"
      : level > 0.5
        ? "var(--color-waveform-q3)"
        : level > 0.25
          ? "var(--color-waveform-q2)"
          : "var(--color-waveform-q1)";

  return (
    <div
      aria-label={live ? "입력 레벨" : "입력 없음"}
      role="img"
      className="flex items-end gap-[2px]"
      style={{ height: 40 }}
    >
      {levels.map((level, index) => (
        <div
          key={index}
          style={{
            width: 3,
            height: `${Math.max(2, level * 40)}px`,
            background: step(level),
            borderRadius: 1,
          }}
        />
      ))}
    </div>
  );
}
