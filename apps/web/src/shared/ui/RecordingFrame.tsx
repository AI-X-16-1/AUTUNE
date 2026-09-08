"use client";

/**
 * The single "recording" signal: a red inset glow on the window frame, on a
 * 3s cycle. It is identical in light and dark, because recording is a state of
 * the meeting and the theme is only a user preference.
 *
 * Paused stops the animation and shows a grey line; ended removes the frame.
 * Under prefers-reduced-motion it becomes a static 2px line (globals.css).
 */
export function RecordingFrame({ state }: { state: "recording" | "paused" | "ended" }) {
  if (state === "ended") return null;
  return (
    <div
      aria-hidden
      className={state === "recording" ? "autune-recording-frame" : undefined}
      style={{
        position: "fixed",
        inset: 0,
        pointerEvents: "none",
        zIndex: 9999,
        animation:
          state === "recording"
            ? `autune-recording-glow var(--recording-glow-duration) ease-in-out infinite`
            : undefined,
        boxShadow: state === "paused" ? "inset 0 0 0 2px rgba(22,25,31,.2)" : undefined,
      }}
    />
  );
}
