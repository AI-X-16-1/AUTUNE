"use client";

import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { CHART_STEPS } from "./chartScale";

/** Between the panel and its target — `--space-8`. */
const GAP = 8;
/** The closest the panel comes to the window's edge. */
const EDGE = 8;
const MAX_WIDTH = 260;

type Box = { top: number; left: number; right: number; bottom: number };
type Size = { width: number; height: number };

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max));
}

/**
 * Where the panel goes, in viewport coordinates (#553). Beside the target on
 * the preferred side when it fits there, else on the other side, else below
 * it — or above, when below is short and above is not. Whatever was chosen is
 * then clamped inside the window, so no part of the panel is ever off screen.
 */
export function placePanel(
  target: Box,
  panel: Size,
  viewport: Size,
  side: "left" | "right",
): { top: number; left: number } {
  const fits = {
    right: target.right + GAP + panel.width <= viewport.width - EDGE,
    left: target.left - GAP - panel.width >= EDGE,
  };
  const beside = (
    side === "right"
      ? (["right", "left"] as const)
      : (["left", "right"] as const)
  ).find((s) => fits[s]);

  let left: number;
  let top: number;
  if (beside) {
    left =
      beside === "right" ? target.right + GAP : target.left - GAP - panel.width;
    top = target.top;
  } else {
    // No room beside it — a full-width row, or a narrow window.
    left = target.left;
    const below = target.bottom + GAP;
    const above = target.top - GAP - panel.height;
    const fitsBelow = below + panel.height <= viewport.height - EDGE;
    top = fitsBelow || above < EDGE ? below : above;
  }
  return {
    left: clamp(left, EDGE, viewport.width - EDGE - panel.width),
    top: clamp(top, EDGE, viewport.height - EDGE - panel.height),
  };
}

/**
 * A small panel shown on hover — a future-state mockup for S26 widgets with
 * no real data yet (heatmap pending #168, prediction pending #26/#27), or
 * supporting detail for a widget that does have data (gap titles behind a
 * pattern's count). No click handling; dismisses when the pointer leaves.
 *
 * `side` is a preference, not a promise: the panel is measured when it opens
 * and placed by `placePanel`, so it opens fully inside the window wherever the
 * card lands in the grid and however wide the window is (#553). It is `fixed`,
 * so a card's own overflow cannot clip it either.
 */
export function HoverPreview({
  mockup,
  label = "완성되면 이런 모습입니다 (예상)",
  side = "right",
  children,
}: {
  mockup: ReactNode;
  label?: string;
  side?: "left" | "right";
  children: ReactNode;
}) {
  const anchor = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{
    top: number;
    left: number;
  } | null>(null);

  const place = useCallback(() => {
    if (!anchor.current || !panel.current) return;
    const size = panel.current.getBoundingClientRect();
    setPosition(
      placePanel(
        anchor.current.getBoundingClientRect(),
        { width: size.width, height: size.height },
        // clientWidth leaves out a vertical scrollbar; innerWidth would not.
        {
          width: document.documentElement.clientWidth,
          height: window.innerHeight,
        },
        side,
      ),
    );
  }, [side]);

  // Before paint, so the panel never shows where it would overflow.
  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  return (
    <div
      ref={anchor}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {children}
      {open ? (
        <div
          ref={panel}
          role="tooltip"
          className="pointer-events-none fixed z-50"
          style={{
            top: position?.top ?? 0,
            left: position?.left ?? 0,
            // Measured at its natural size first, shown once it has a place.
            visibility: position ? "visible" : "hidden",
            width: "max-content",
            maxWidth: `min(${MAX_WIDTH}px, calc(100vw - ${2 * EDGE}px))`,
            maxHeight: `calc(100vh - ${2 * EDGE}px)`,
            overflow: "hidden",
            background: "var(--color-surface-panel)",
            border: "1px solid var(--color-hairline)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--shadow-overlay)",
            padding: "var(--space-8)",
          }}
        >
          <p
            style={{
              margin: 0,
              marginBottom: "var(--space-4)",
              fontSize: "var(--text-metaSmall)",
              color: "var(--color-ink-muted)",
            }}
          >
            {label}
          </p>
          {mockup}
        </div>
      ) : null}
    </div>
  );
}

/** Illustrative role-pair grid — same shape as the real AlignmentHeatmap. */
export function HeatmapMockup(): ReactNode {
  const roles = ["PM", "Dev"];
  const values = [
    [0.8, 0.6],
    [0.4],
  ];
  return (
    <svg width={240} height={155} role="img" aria-label="히트맵 예상 모습">
      <g fontSize={15} fill="var(--color-ink-muted)">
        <text x={75} y={17}>
          Dev
        </text>
        <text x={153} y={17}>
          Design
        </text>
        <text x={3} y={48}>
          {roles[0]}
        </text>
        <text x={3} y={92}>
          {roles[1]}
        </text>
      </g>
      {values.map((row, rowIndex) =>
        row.map((value, colIndex) => (
          <rect
            key={`${rowIndex}-${colIndex}`}
            x={61 + colIndex * 78}
            y={27 + rowIndex * 44}
            width={68}
            height={34}
            rx={3}
            fill={CHART_STEPS[Math.min(4, Math.floor(value * 5))]}
          />
        )),
      )}
    </svg>
  );
}

/** Illustrative probability stat — matches ui-spec's "prediction, probability in mono". */
export function PredictionMockup(): ReactNode {
  return (
    <svg width={240} height={120} role="img" aria-label="예측 예상 모습">
      <text
        x={0}
        y={44}
        fontFamily="var(--font-mono)"
        fontSize={37}
        fontWeight="var(--text-title-weight)"
        fill="var(--color-ink-strong)"
      >
        62%
      </text>
      <text x={0} y={71} fontSize={15} fill="var(--color-ink-muted)">
        정렬 붕괴 위험 · 2주 이내
      </text>
      <rect x={0} y={85} width={221} height={10} rx={5} fill="var(--color-surface-sunken)" />
      <rect x={0} y={85} width={136} height={10} rx={5} fill={CHART_STEPS[3]} />
    </svg>
  );
}

/** The real gap titles behind one pattern's count — not a mockup. */
export function GapTitleList({ titles }: { titles: string[] }): ReactNode {
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none", maxWidth: 220 }}>
      {titles.map((title, index) => (
        <li
          key={index}
          style={{
            margin: "2px 0",
            fontSize: "var(--text-metaSmall)",
            color: "var(--color-ink-body)",
          }}
        >
          · {title}
        </li>
      ))}
    </ul>
  );
}
