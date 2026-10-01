"use client";

import { Button } from "@/shared/ui";

import { timecode } from "../format";
import { KIND_LABELS, type RecordingState, type UtteranceKind } from "../types";
import { KindMark } from "./KindTag";

/**
 * The right rail: how long this has been running, what it has found, and the
 * two controls that change the recording.
 *
 * The timer is 56px and monospace because it is the one number somebody glances
 * at from across a table. It is in ink, not red: red on this screen is the
 * recording itself -- the frame, the pulsing dot above the timer, the elapsed
 * bar -- and a red timer would make every glance read as an alarm. It is also
 * why the stop button is not red: red is the state, not the action.
 *
 * "원본 즉시 삭제" in the header is a fact of this path, not a promise: live
 * audio is held as frames in memory and dropped as each segment is
 * transcribed (`autune_audio.live`), and the uploaded recording is deleted
 * when its transcription completes (`privacy.md`).
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
   * and found none" — `모호 0` reads as a claim that nothing ambiguous was
   * said. This screen says what it knows and nothing else. */
  onPause?: () => void;
  onResume?: () => void;
  onStop?: () => void;
}) {
  const progress =
    plannedSeconds && plannedSeconds > 0
      ? Math.min(1, elapsedSeconds / plannedSeconds)
      : undefined;
  const recording = state === "recording";

  return (
    <aside className="flex flex-col" aria-label="녹음">
      <div
        style={{
          padding: "22px var(--space-card) 18px",
          borderBottom: "1px solid var(--color-hairline)",
        }}
      >
        <div className="flex items-center" style={{ gap: "var(--space-8)" }}>
          {/* The dot pulses on the same 3s cycle as the frame; under
              prefers-reduced-motion it holds still. */}
          <span
            aria-hidden
            className={`shrink-0 rounded-full ${
              recording ? "animate-[pulse_3s_ease-in-out_infinite] motion-reduce:animate-none" : ""
            }`}
            style={{
              width: 9,
              height: 9,
              background: recording
                ? "var(--color-signal-critical)"
                : "var(--color-signal-idle)",
            }}
          />
          <span
            style={{
              fontSize: "var(--text-rowTitle)",
              fontWeight: "var(--text-rowTitle-weight)",
              color: recording ? "var(--color-signal-critical)" : "var(--color-ink-muted)",
            }}
          >
            {recording ? "녹음 중" : state === "paused" ? "일시정지" : "녹음 종료"}
          </span>
          <div className="flex-1" />
          <span
            style={{
              fontSize: "var(--text-label)",
              fontWeight: "var(--text-meta-weight)",
              color: "var(--color-ink-muted)",
            }}
          >
            원본 즉시 삭제
          </span>
        </div>

        <div
          className="tabular-nums"
          style={{
            marginTop: "var(--space-12)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-timer)",
            fontWeight: "var(--text-timer-weight)",
            lineHeight: "var(--text-timer-leading)",
            letterSpacing: "var(--text-timer-tracking)",
            color: recording ? "var(--color-ink-strong)" : "var(--color-ink-muted)",
          }}
        >
          {timecode(elapsedSeconds)}
        </div>

        {progress === undefined || plannedSeconds === undefined ? null : (
          <>
            <div
              className="flex justify-between"
              style={{
                marginTop: "var(--space-12)",
                fontSize: "var(--text-meta)",
                color: "var(--color-ink-muted)",
              }}
            >
              <span>
                예정 {Math.round(plannedSeconds / 60)}분 중{" "}
                <span style={{ fontFamily: "var(--font-mono)" }}>{Math.round(progress * 100)}%</span>
              </span>
              <span>
                남은{" "}
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--color-ink-strong)" }}>
                  {timecode(Math.max(0, plannedSeconds - elapsedSeconds))}
                </span>
              </span>
            </div>
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress * 100)}
              aria-label="예정 시간 대비 경과"
              style={{
                marginTop: "var(--space-8)",
                height: "var(--bar-thickness)",
                background: "var(--color-hairline)",
              }}
            >
              <div
                style={{
                  width: `${progress * 100}%`,
                  height: "100%",
                  background: "var(--color-signal-critical)",
                }}
              />
            </div>
          </>
        )}

        <div style={{ marginTop: "var(--space-row)" }}>
          <Waveform levels={levels} live={recording} />
        </div>

        {/* Two equal 40px halves across the rail, as S13 draws them: the
            controls someone reaches for mid-meeting, sized to be hit without
            looking. */}
        <div className="flex items-center gap-1" style={{ marginTop: "var(--space-row)" }}>
          {recording ? (
            <Button tone="secondary" className="flex-1" onClick={onPause}>
              일시정지
            </Button>
          ) : null}
          {state === "paused" ? (
            <Button tone="secondary" className="flex-1" onClick={onResume}>
              이어서 녹음
            </Button>
          ) : null}
          {state === "ended" ? null : (
            <Button tone="primary" className="flex-1" onClick={onStop}>
              녹음 종료
            </Button>
          )}
        </div>
      </div>

      <section style={{ padding: "var(--space-16) var(--space-card)" }} aria-label="감지">
        <div
          style={{
            fontSize: "var(--text-rowTitle)",
            fontWeight: "var(--text-rowTitle-weight)",
            color: "var(--color-ink-strong)",
          }}
        >
          감지{" "}
          <span
            style={{
              marginLeft: "var(--space-4)",
              fontSize: "var(--text-label)",
              fontWeight: "var(--text-meta-weight)",
              color: "var(--color-ink-muted)",
            }}
          >
            모듈 B
          </span>
        </div>
        {counts === undefined ? (
          <p
            style={{
              marginTop: "var(--space-8)",
              fontSize: "var(--text-meta)",
              color: "var(--color-ink-muted)",
            }}
          >
            회의가 끝나면 분류됩니다
          </p>
        ) : (
          <dl
            className="grid"
            style={{
              gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
              gap: 6,
              marginTop: "var(--space-12)",
            }}
          >
            {(Object.keys(KIND_LABELS) as UtteranceKind[]).map((kind) => (
              <div key={kind} className="flex flex-col-reverse">
                <dt
                  className="flex items-center"
                  style={{
                    gap: 5,
                    marginTop: 3,
                    fontSize: "var(--text-metaSmall)",
                    color: "var(--color-ink-muted)",
                  }}
                >
                  <KindMark kind={kind} />
                  {KIND_LABELS[kind].tally}
                </dt>
                <dd
                  className="tabular-nums"
                  style={{
                    fontFamily: "var(--font-mono)",
                    // S13 draws 18px; the nearest step in the type scale is
                    // the 20px title, and a size outside the scale is a
                    // token change, not a local style.
                    fontSize: "var(--text-title)",
                    fontWeight: "var(--text-timer-weight)",
                    lineHeight: "var(--text-title-leading)",
                    color:
                      kind === "ambiguous"
                        ? "var(--color-signal-attention)"
                        : "var(--color-ink-strong)",
                  }}
                >
                  {counts[kind] ?? 0}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </section>
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
      style={{ height: 28 }}
    >
      {levels.map((level, index) => (
        <div
          key={index}
          style={{
            width: 4,
            height: `${Math.max(2, level * 28)}px`,
            background: step(level),
          }}
        />
      ))}
    </div>
  );
}
