"use client";

import { useCallback, useRef, useState } from "react";

import { Button } from "@/shared/ui";

import { listJiraOpenIssues, type JiraIssue, type JiraProjectIssues } from "../api";
import { shownDue } from "../dates";

/**
 * What is already in the team's Jira, beside what Autune extracted (the user,
 * 2026-10-02: "원래 지라에 있던 내용을 가져올 수 있게", then, asked which
 * way, "보기만: 연결한 프로젝트의 열린 이슈 목록").
 *
 * **Viewed, not imported.** The server reads the connected project's open
 * issues from Jira when this is opened and passes them through; nothing is
 * stored, and this component keeps them only while it is mounted. So there is
 * no copy to fall out of step with Jira, and nothing of the team's Jira for
 * Autune to retain or delete.
 *
 * Closed until asked for: every open is a request to Jira with the team's
 * connection, and the board above it is the page's subject.
 *
 * Under a board showing one team (`teamId`, 2026-10-08) only that team's
 * project is listed. The request is the same one -- every team's is read --
 * and the others are left out of what is shown.
 */

type State =
  | { kind: "closed" }
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "ready"; projects: JiraProjectIssues[] };

const WHY_EMPTY: Record<Exclude<JiraProjectIssues["state"], "ok">, string> = {
  no_project: "Jira 프로젝트를 아직 고르지 않았습니다. 회의의 액션 탭에서 고를 수 있습니다.",
  needs_reconnect: "Jira 연결이 끊어졌습니다. 회의의 액션 탭에서 다시 연결해 주세요.",
  unavailable: "Jira가 응답하지 않습니다. 잠시 후 다시 불러와 주세요.",
};

const meta = { fontSize: "var(--text-metaSmall)" } as const;
const body = { fontSize: "var(--text-rowBody)", lineHeight: "var(--text-rowBody-leading)" } as const;

export function JiraOpenIssues({ teamId = null }: { teamId?: string | null }) {
  const [state, setState] = useState<State>({ kind: "closed" });
  // Which request an answer belongs to. Collapsing moves it on, so an answer
  // that arrives after 접기 is dropped instead of opening the list again.
  const request = useRef(0);

  const load = useCallback(() => {
    const mine = ++request.current;
    setState({ kind: "loading" });
    listJiraOpenIssues()
      .then((projects) => {
        if (request.current === mine) setState({ kind: "ready", projects });
      })
      .catch(() => {
        if (request.current === mine) setState({ kind: "failed" });
      });
  }, []);

  const collapse = useCallback(() => {
    request.current += 1;
    setState({ kind: "closed" });
  }, []);

  if (state.kind === "closed") {
    return (
      <section aria-label="Jira 열린 이슈" style={{ marginTop: "var(--space-24)" }}>
        <Button tone="text" size="compact" onClick={load}>
          Jira 열린 이슈 보기
        </Button>
      </section>
    );
  }
  const projects =
    state.kind !== "ready"
      ? []
      : state.projects.filter((project) => teamId === null || project.team_id === teamId);

  return (
    <section aria-label="Jira 열린 이슈" style={{ marginTop: "var(--space-24)" }}>
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 className="text-[var(--color-ink-strong)]" style={body}>
          Jira 열린 이슈
        </h2>
        <Button tone="text" size="compact" onClick={load} disabled={state.kind === "loading"}>
          다시 불러오기
        </Button>
        <Button tone="text" size="compact" onClick={collapse}>
          접기
        </Button>
      </div>
      <p className="mb-3 text-[var(--color-ink-muted)]" style={meta}>
        Jira에서 지금 읽어 보여 주기만 합니다. Autune에 저장하지 않습니다.
      </p>

      {state.kind === "loading" ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          Jira에서 불러오는 중입니다.
        </p>
      ) : state.kind === "failed" ? (
        <p role="alert" className="text-[var(--color-signal-critical)]" style={meta}>
          Jira 이슈를 불러오지 못했습니다. 잠시 후 다시 불러와 주세요.
        </p>
      ) : projects.length === 0 ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          연결한 Jira 프로젝트가 없습니다. 회의의 액션 탭에서 Jira를 연결하면 열린 이슈가 이곳에
          보입니다.
        </p>
      ) : (
        projects.map((project) => <Project key={project.team_id} project={project} />)
      )}
    </section>
  );
}

function Project({ project }: { project: JiraProjectIssues }) {
  return (
    <div className="mb-4">
      <h3 className="mb-1 text-[var(--color-ink-muted)]" style={meta}>
        {project.team_name}
        {project.project_key ? ` · ${project.project_key}` : ""}
      </h3>
      {project.state !== "ok" ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          {WHY_EMPTY[project.state]}
        </p>
      ) : project.issues.length === 0 ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          열린 이슈가 없습니다.
        </p>
      ) : (
        <ul className="border-t border-[var(--color-hairline)]">
          {project.issues.map((issue) => (
            <IssueRow key={issue.key} issue={issue} />
          ))}
        </ul>
      )}
      {project.more ? (
        <p className="mt-2 text-[var(--color-ink-muted)]" style={meta}>
          최근에 바뀐 이슈부터 일부만 보여 줍니다. 나머지는 Jira에서 확인해 주세요.
        </p>
      ) : null}
    </div>
  );
}

function IssueRow({ issue }: { issue: JiraIssue }) {
  return (
    <li
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-[var(--color-hairline)]"
      style={{ paddingBlock: "var(--space-8)" }}
    >
      {issue.url ? (
        <a
          className="font-mono text-[var(--color-accent-default)]"
          style={meta}
          href={issue.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          {issue.key}
        </a>
      ) : (
        <span className="font-mono text-[var(--color-ink-muted)]" style={meta}>
          {issue.key}
        </span>
      )}
      <span className="min-w-0 flex-1 text-[var(--color-ink-strong)]" style={body}>
        {issue.summary}
      </span>
      {issue.from_autune ? (
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          Autune에서 만든 이슈
        </span>
      ) : null}
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        {issue.status ?? "상태 없음"} · {issue.assignee ?? "담당자 없음"} ·{" "}
        {issue.due_date ? shownDue(issue.due_date) : "기한 없음"}
      </span>
    </li>
  );
}
