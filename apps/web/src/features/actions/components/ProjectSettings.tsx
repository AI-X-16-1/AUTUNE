"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import {
  createProject,
  deleteProject,
  listProjects,
  updateProject,
} from "../api";
import type { Project } from "../types";
import { NameSuggestions } from "./NameSuggestions";

/**
 * The team's projects on S28 (the user, 2026-10-04): a meeting that covers
 * several projects is summarised — and later sent out — project by project,
 * and these are the projects it is split into. A name, the other names people
 * say for it (matched in what was said), and optionally its own Jira project.
 */
export function ProjectSettings({ teamId }: { teamId: string }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listProjects({ teamId })
      .then((list) => alive && setProjects(list))
      .catch(() => alive && setNote("프로젝트를 불러오지 못했습니다."));
    return () => {
      alive = false;
    };
  }, [teamId]);

  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  if (projects === null) {
    return note ? (
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        {note}
      </p>
    ) : null;
  }

  const refused = (cause: unknown) =>
    setNote(
      cause instanceof ApiError && cause.status === 409
        ? "같은 이름의 프로젝트가 이미 있습니다."
        : "저장하지 못했습니다. 이름과 Jira 키를 확인해 주세요.",
    );

  return (
    <div className="flex flex-col gap-2" aria-label="프로젝트">
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        회의에서 여러 프로젝트 얘기가 나오면 결정과 할 일을 이 프로젝트별로
        나눕니다. 별칭은 회의에서 그 프로젝트를 부르는 다른 이름입니다.
      </p>
      <ul className="flex flex-col gap-2">
        {projects.map((project) => (
          <ProjectRow
            key={project.id}
            project={project}
            onSave={(draft) =>
              updateProject(teamId, project.id, draft)
                .then((saved) => {
                  setProjects((list) =>
                    (list ?? []).map((p) => (p.id === saved.id ? saved : p)),
                  );
                  setNote("저장했습니다.");
                })
                .catch(refused)
            }
            onDelete={() =>
              deleteProject(teamId, project.id)
                .then(() => {
                  setProjects((list) =>
                    (list ?? []).filter((p) => p.id !== project.id),
                  );
                  setNote(
                    "삭제했습니다. 이 프로젝트에 있던 항목은 미분류가 됩니다.",
                  );
                })
                .catch(() => setNote("삭제하지 못했습니다."))
            }
          />
        ))}
      </ul>
      <ProjectRow
        onSave={(draft) =>
          createProject(teamId, draft)
            .then((saved) => {
              setProjects((list) => [...(list ?? []), saved]);
              setNote("프로젝트를 추가했습니다.");
            })
            .catch(refused)
        }
      />
      <NameSuggestions
        teamId={teamId}
        projects={projects}
        onChanged={(saved) =>
          setProjects((list) => {
            const rest = (list ?? []).filter((p) => p.id !== saved.id);
            return [...rest, saved];
          })
        }
      />
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

/** One project's fields, or an empty row that adds one. */
function ProjectRow({
  project,
  onSave,
  onDelete,
}: {
  project?: Project;
  onSave: (draft: {
    name: string;
    aliases: string[];
    jira_project_key: string | null;
  }) => Promise<void>;
  onDelete?: () => Promise<void>;
}) {
  const [name, setName] = useState(project?.name ?? "");
  const [aliases, setAliases] = useState((project?.aliases ?? []).join(", "));
  const [jira, setJira] = useState(project?.jira_project_key ?? "");
  const [busy, setBusy] = useState(false);
  const input = {
    fontSize: "var(--text-metaSmall)",
    borderRadius: "var(--radius)",
    padding: "4px 8px",
  } as const;

  const save = async () => {
    setBusy(true);
    try {
      await onSave({
        name,
        aliases: aliases
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean),
        jira_project_key: jira.trim() || null,
      });
      if (!project) {
        setName("");
        setAliases("");
        setJira("");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <li
      className="flex flex-wrap items-center gap-2"
      style={{ listStyle: "none" }}
    >
      {/* `max-w-full`: a text field is as wide as its twenty characters whatever
          holds it, and in a column narrower than that the two here pushed the
          page sideways (390 px screen). They stop at the row's width instead. */}
      <input
        aria-label="프로젝트 이름"
        placeholder={project ? undefined : "새 프로젝트 이름"}
        value={name}
        maxLength={100}
        onChange={(event) => setName(event.target.value)}
        className="max-w-full border border-[var(--color-hairline)]"
        style={input}
      />
      <input
        aria-label="별칭"
        placeholder="별칭 (쉼표로 구분)"
        value={aliases}
        onChange={(event) => setAliases(event.target.value)}
        className="max-w-full border border-[var(--color-hairline)]"
        style={input}
      />
      <input
        aria-label="Jira 프로젝트 키"
        placeholder="Jira 키 (선택)"
        value={jira}
        maxLength={32}
        onChange={(event) => setJira(event.target.value)}
        className="w-28 border border-[var(--color-hairline)]"
        style={input}
      />
      <Button
        tone={project ? "quiet" : "text"}
        size="compact"
        loading={busy}
        disabled={!name.trim()}
        onClick={() => void save()}
      >
        {project ? "저장" : "추가"}
      </Button>
      {onDelete ? (
        <Button tone="quiet" size="compact" onClick={() => void onDelete()}>
          삭제
        </Button>
      ) : null}
    </li>
  );
}
