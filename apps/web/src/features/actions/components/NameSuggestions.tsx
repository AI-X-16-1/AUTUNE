"use client";

import { useEffect, useState } from "react";

import { createProject, listProjectSuggestions, updateProject } from "../api";
import type { Project } from "../types";

const NEW = "__new__";

/**
 * Words said often in the team's latest meetings that no project is named or
 * aliased by (the user, 2026-10-04) -- each one can become a project, or an
 * alias of one. Words and counts only; the server never sends a sentence.
 */
export function NameSuggestions({
  teamId,
  projects,
  onChanged,
}: {
  teamId: string;
  projects: Project[];
  onChanged: (project: Project) => void;
}) {
  const [words, setWords] = useState<{ word: string; count: number }[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  useEffect(() => {
    let alive = true;
    listProjectSuggestions(teamId)
      .then((list) => alive && setWords(list))
      .catch(() => alive && setWords([]));
    return () => {
      alive = false;
    };
  }, [teamId]);

  if (words.length === 0 && note === null) return null;

  const use = async (word: string, target: string) => {
    try {
      const saved =
        target === NEW
          ? await createProject(teamId, { name: word, aliases: [] })
          : await (() => {
              const project = projects.find((p) => p.id === target);
              if (!project) throw new Error("gone");
              return updateProject(teamId, project.id, {
                name: project.name,
                aliases: [...project.aliases, word],
                jira_project_key: project.jira_project_key,
              });
            })();
      onChanged(saved);
      setWords((list) => list.filter((w) => w.word !== word));
      setNote(
        target === NEW
          ? `"${word}" 프로젝트를 만들었습니다.`
          : `"${word}"를 ${saved.name}의 별칭에 넣었습니다.`,
      );
    } catch {
      setNote("넣지 못했습니다. 이미 같은 이름이 있는지 확인해 주세요.");
    }
  };

  return (
    <div aria-label="회의에 자주 나온 말" className="flex flex-col gap-2">
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        회의에 자주 나왔지만 아직 프로젝트 이름이나 별칭이 아닌 말입니다.
      </p>
      {words.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {words.map(({ word, count }) => (
            <li
              key={word}
              className="flex items-center gap-1 border border-[var(--color-hairline)] px-2 py-1"
              style={{
                ...meta,
                borderRadius: "var(--radius)",
                listStyle: "none",
              }}
            >
              <span className="text-[var(--color-ink-body)]">{word}</span>
              <span className="text-[var(--color-ink-muted)]">{count}회</span>
              <select
                aria-label={`${word} 넣기`}
                value=""
                onChange={(event) => void use(word, event.target.value)}
              >
                <option value="" disabled>
                  넣기…
                </option>
                <option value={NEW}>새 프로젝트로</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} 별칭으로
                  </option>
                ))}
              </select>
            </li>
          ))}
        </ul>
      ) : null}
      {note ? (
        <span
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={meta}
        >
          {note}
        </span>
      ) : null}
    </div>
  );
}
