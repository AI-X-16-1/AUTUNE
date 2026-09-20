import { Fragment } from "react";

import { CHART_STEPS, stepFor } from "./chartScale";
import { DashboardCard } from "./DashboardCard";
import { HeatmapMockup, HoverPreview } from "./HoverPreview";
import type { HeatmapCell } from "../types";

const CELL = 30;

/**
 * The role-pair grid on S26 — upper-triangle only, horizontally scrollable.
 *
 * `role_a`/`role_b` is an unordered pair (`docs/product/glossary.md`), so a
 * full N×N square would draw every pair twice and keep shrinking its cells as
 * roles are added. Half the matrix at a fixed cell size instead: it never
 * gets illegible, and a team with more roles scrolls within the card rather
 * than crushing every other widget's height to fit.
 */
export function AlignmentHeatmap({ cells }: { cells: HeatmapCell[] }) {
  if (cells.length === 0) {
    return (
      <HoverPreview mockup={<HeatmapMockup />}>
        <DashboardCard title="직무 쌍 얼라인먼트">
          <p style={{ margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" }}>
            아직 역할 간 정렬도를 계산하지 않습니다 (#168).
          </p>
        </DashboardCard>
      </HoverPreview>
    );
  }

  const roles = uniqueRoles(cells);
  const score = scoreLookup(cells);
  const columns = roles.slice(1);
  const rows = roles.slice(0, -1);

  return (
    <DashboardCard title="직무 쌍 얼라인먼트">
      <div className="overflow-x-auto pb-1">
        <div
          className="grid gap-1"
          style={{
            gridTemplateColumns: `40px repeat(${columns.length}, ${CELL}px)`,
            width: "max-content",
          }}
        >
          <div />
          {columns.map((role) => (
            <div
              key={role}
              className="flex items-center justify-center"
              style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-ink-muted)" }}
            >
              {role}
            </div>
          ))}
          {rows.map((rowRole, rowIndex) => (
            <Fragment key={rowRole}>
              <div
                className="flex items-center"
                style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-ink-muted)" }}
              >
                {rowRole}
              </div>
              {columns.map((colRole, colIndex) => {
                if (colIndex < rowIndex) {
                  return <div key={colRole} style={{ width: CELL, height: CELL }} />;
                }
                const value = score(rowRole, colRole);
                const label = `${rowRole} × ${colRole}${value != null ? ` · ${Math.round(value * 100)}%` : " · 데이터 없음"}`;
                return (
                  <div
                    key={colRole}
                    role="img"
                    aria-label={label}
                    title={label}
                    style={{
                      width: CELL,
                      height: CELL,
                      borderRadius: 2,
                      background: value != null ? stepFor(value) : "var(--color-surface-sunken)",
                    }}
                  />
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>
      <div
        className="flex items-center gap-2"
        style={{
          marginTop: "var(--space-12)",
          paddingTop: "var(--space-12)",
          borderTop: "1px solid var(--color-hairline)",
        }}
      >
        <span style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-ink-muted)" }}>
          낮음
        </span>
        {CHART_STEPS.map((color) => (
          <span
            key={color}
            style={{ width: 16, height: 10, borderRadius: 1, background: color }}
          />
        ))}
        <span style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-ink-muted)" }}>
          높음
        </span>
      </div>
    </DashboardCard>
  );
}

function uniqueRoles(cells: HeatmapCell[]): string[] {
  const roles = new Set<string>();
  for (const cell of cells) {
    roles.add(cell.role_a);
    roles.add(cell.role_b);
  }
  return [...roles].sort();
}

/** Looks a pair up regardless of which side the backend stored as `role_a`. */
function scoreLookup(cells: HeatmapCell[]): (a: string, b: string) => number | null {
  const byPair = new Map<string, number>();
  for (const cell of cells) {
    byPair.set(`${cell.role_a}::${cell.role_b}`, cell.score);
    byPair.set(`${cell.role_b}::${cell.role_a}`, cell.score);
  }
  return (a, b) => byPair.get(`${a}::${b}`) ?? null;
}
