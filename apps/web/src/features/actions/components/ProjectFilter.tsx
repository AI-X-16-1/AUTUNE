"use client";

import { ALL_PROJECTS, UNSORTED, type ProjectChoice } from "../projectFilter";
import type { Project } from "../types";

/**
 * The board's project filter (the user, 2026-10-04): everything, one of the
 * team's projects, or what no project took (미분류). Not drawn for a team
 * that lists no projects -- there is nothing to choose between.
 *
 * `teams` is the team to say beside a project's name (`projectTeams`): the
 * board across meetings lists several teams' projects, and two of them can
 * have one name.
 */
export function ProjectFilter({
  projects,
  teams,
  value,
  onChange,
}: {
  projects: Project[];
  teams?: Map<string, string>;
  value: ProjectChoice;
  onChange: (choice: ProjectChoice) => void;
}) {
  if (projects.length === 0) return null;
  return (
    <label
      className="flex items-center gap-2 text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      프로젝트
      <select
        aria-label="프로젝트로 거르기"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value={ALL_PROJECTS}>전체</option>
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {teams?.has(project.id)
              ? `${project.name} · ${teams.get(project.id)}`
              : project.name}
          </option>
        ))}
        <option value={UNSORTED}>미분류</option>
      </select>
    </label>
  );
}
