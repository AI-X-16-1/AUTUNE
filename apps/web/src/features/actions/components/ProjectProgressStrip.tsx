"use client";

import { ALL_PROJECTS, type ProjectChoice } from "../projectFilter";
import type { ProjectProgress } from "../projectProgress";

/**
 * A line per project above the team board (the user, 2026-10-04): how much of
 * the agreed work is done and how much is late. Choosing a project filters the
 * board to it; choosing it again clears the filter. Not drawn when no project has a confirmed item.
 */
export function ProjectProgressStrip({
  lines,
  value,
  onChoose,
}: {
  lines: ProjectProgress[];
  value: ProjectChoice;
  onChoose: (choice: ProjectChoice) => void;
}) {
  if (lines.length === 0) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  return (
    <ul aria-label="프로젝트 진행" className="mt-3 flex flex-wrap gap-2">
      {lines.map(({ project, total, done, overdue }) => {
        const percent = Math.round((done / total) * 100);
        const chosen = value === project.id;
        return (
          <li key={project.id} style={{ listStyle: "none" }}>
            <button
              type="button"
              aria-pressed={chosen}
              onClick={() => onChoose(chosen ? ALL_PROJECTS : project.id)}
              className="flex min-w-40 flex-col gap-1 border px-3 py-2 text-left"
              style={{
                ...meta,
                borderRadius: "var(--radius)",
                borderColor: chosen
                  ? "var(--color-ink-strong)"
                  : "var(--color-hairline)",
              }}
            >
              <span
                className="text-[var(--color-ink-strong)]"
                style={{ fontWeight: 600 }}
              >
                {project.name}
              </span>
              <span
                role="progressbar"
                aria-label={`${project.name} 완료율`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={percent}
                className="block h-1 w-full bg-[var(--color-hairline)]"
                style={{ borderRadius: "var(--radius)" }}
              >
                <span
                  className="block h-1 bg-[var(--color-ink-strong)]"
                  style={{
                    width: `${percent}%`,
                    borderRadius: "var(--radius)",
                  }}
                />
              </span>
              <span className="text-[var(--color-ink-muted)]">
                완료 {done}/{total}
                {overdue > 0 ? (
                  <span className="text-[var(--color-signal-critical)]">
                    {" "}
                    · 기한 초과 {overdue}
                  </span>
                ) : null}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
