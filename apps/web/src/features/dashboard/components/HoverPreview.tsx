import type { ReactNode } from "react";

import { CHART_STEPS } from "./chartScale";

/**
 * A small panel shown on hover — a future-state mockup for S26 widgets with
 * no real data yet (heatmap pending #168, prediction/influence map pending
 * #26/#27), or supporting detail for a widget that does have data (gap
 * titles behind a pattern's count). Pure CSS hover — no state, no click
 * handling, dismisses when the pointer leaves.
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
  return (
    <div className="group relative">
      {children}
      <div
        className={`pointer-events-none absolute top-0 z-10 hidden group-hover:block ${
          side === "right" ? "left-full" : "right-full"
        }`}
        style={{
          marginLeft: side === "right" ? "var(--space-8)" : undefined,
          marginRight: side === "left" ? "var(--space-8)" : undefined,
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

/** Illustrative node-link mockup — matches ui-spec's "influence map, role level only". */
export function InfluenceMapMockup(): ReactNode {
  const nodes: { label: string; x: number; y: number }[] = [
    { label: "PM", x: 119, y: 24 },
    { label: "Dev", x: 41, y: 102 },
    { label: "Design", x: 197, y: 102 },
  ];
  const edges: [number, number][] = [
    [0, 1],
    [0, 2],
    [1, 2],
  ];
  return (
    <svg width={240} height={155} role="img" aria-label="영향력 맵 예상 모습">
      {edges.map(([a, b]) => {
        const from = nodes[a];
        const to = nodes[b];
        if (!from || !to) return null;
        return (
          <line
            key={`${a}-${b}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke="var(--color-hairline)"
            strokeWidth={2}
          />
        );
      })}
      {nodes.map((node) => (
        <g key={node.label}>
          <circle cx={node.x} cy={node.y} r={20} fill={CHART_STEPS[2]} />
          <text
            x={node.x}
            y={node.y + 41}
            fontSize={15}
            textAnchor="middle"
            fill="var(--color-ink-muted)"
          >
            {node.label}
          </text>
        </g>
      ))}
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
