"use client";

import { useCallback, useRef, useState } from "react";

import { Button } from "@/shared/ui";

import {
  listJiraOpenIssues,
  listSinceLastMeeting,
  type JiraProjectIssues,
  type SinceLastMeeting,
} from "../api";
import { localToday, shownDue } from "../dates";
import { SINCE_PARTS } from "../sinceParts";
import { rowTitle } from "../title";
import type { ActionItemRead } from "../types";

/**
 * What moved on the board since each team's last meeting (the user,
 * 2026-10-12: "사용자들이 회의할 때 전체 작업 내용 요약"): what was finished,
 * what was closed without being finished, what is late, what was started and
 * what is new, then how many are open -- on the board and in the team's Jira.
 *
 * **Read when asked for, kept nowhere.** The server sorts Autune's own items
 * by the edits the board recorded and answers with ids; nothing is stored, and
 * this component keeps the answer only while it is mounted. It is as old as
 * the last press of 다시 불러오기: a card moved since is not followed.
 *
 * **The rows are the board's items, without their owner.** An id is drawn
 * from the item the board already holds (`items`) as its title and its due
 * date, and opens the same drawer. **A row names nobody** (the user,
 * 2026-10-12: "담당자 빼기"): lined up under "끝난 일" since a meeting, rows
 * with a name on each would read as each person's record since that meeting,
 * which no single card says. Who holds an item is on its card and in the
 * drawer the row opens. Nothing here is grouped, counted or ordered by
 * person: a part's count is the team's. An id the board does not hold --
 * deleted since, or another team's under a board of one -- is left out.
 *
 * **The Jira count is the open-issues read, not a new one.** Opening this
 * asks what `JiraOpenIssues` asks and keeps the number alone. A team with no
 * project, or whose Jira did not answer, gets no Jira count; that read failing
 * never fails this section.
 *
 * Closed until asked for, as the Jira list under it: the board above is the
 * page's subject, and an open is a request to Jira with the team's connection.
 */

type Ready = { kind: "ready"; answers: SinceLastMeeting[]; jira: JiraProjectIssues[] };
type State = { kind: "closed" } | { kind: "loading" } | { kind: "failed" } | Ready;

const meta = { fontSize: "var(--text-metaSmall)" } as const;
const body = { fontSize: "var(--text-rowBody)", lineHeight: "var(--text-rowBody-leading)" } as const;

export function SinceLastMeetingSection({
  items,
  teamId = null,
  onOpen,
}: {
  /** The board's items: what an id in the answer is drawn from. */
  items: ActionItemRead[];
  teamId?: string | null;
  /** Open one item as its card does. */
  onOpen: (id: string) => void;
}) {
  const [state, setState] = useState<State>({ kind: "closed" });
  // Which request an answer belongs to. Collapsing moves it on, so an answer
  // that arrives after 접기 is dropped instead of opening the section again.
  const request = useRef(0);

  const load = useCallback(() => {
    const mine = ++request.current;
    setState({ kind: "loading" });
    Promise.all([listSinceLastMeeting(), listJiraOpenIssues().catch(() => [])])
      .then(([answers, jira]) => {
        if (request.current === mine) setState({ kind: "ready", answers, jira });
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
      <section aria-label="지난 회의 이후" style={{ marginTop: "var(--space-24)" }}>
        <Button tone="text" size="compact" onClick={load}>
          지난 회의 이후 보기
        </Button>
      </section>
    );
  }
  const answers =
    state.kind !== "ready"
      ? []
      : state.answers.filter((answer) => teamId === null || answer.team_id === teamId);
  const byId = new Map(items.map((item) => [item.id, item]));

  return (
    <section aria-label="지난 회의 이후" style={{ marginTop: "var(--space-24)" }}>
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 className="text-[var(--color-ink-strong)]" style={body}>
          지난 회의 이후
        </h2>
        <Button tone="text" size="compact" onClick={load} disabled={state.kind === "loading"}>
          다시 불러오기
        </Button>
        <Button tone="text" size="compact" onClick={collapse}>
          접기
        </Button>
      </div>
      <p className="mb-3 text-[var(--color-ink-muted)]" style={meta}>
        보드에 남은 변경 기록에서 지금 읽어 보여 주기만 합니다. 따로 저장하지 않습니다.
      </p>

      {state.kind === "loading" ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          불러오는 중입니다.
        </p>
      ) : state.kind === "failed" ? (
        <p role="alert" className="text-[var(--color-signal-critical)]" style={meta}>
          지난 회의 이후의 변경을 불러오지 못했습니다. 잠시 후 다시 불러와 주세요.
        </p>
      ) : answers.length === 0 ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          보여 줄 팀이 없습니다.
        </p>
      ) : (
        answers.map((answer) => (
          <Team
            key={answer.team_id}
            answer={answer}
            jira={state.kind === "ready" ? state.jira : []}
            byId={byId}
            onOpen={onOpen}
          />
        ))
      )}
    </section>
  );
}

/** The day an instant falls on where the viewer is, written as a due date is. */
function heldOn(since: string): string {
  const at = new Date(since);
  return Number.isNaN(at.getTime()) ? "" : shownDue(localToday(at));
}

/** The team's open Jira issues as a count, or null when there is none to say. */
function jiraOpen(jira: JiraProjectIssues[], teamId: string): string | null {
  const project = jira.find((one) => one.team_id === teamId);
  if (project === undefined || project.state !== "ok") return null;
  return `Jira 열린 이슈 ${project.issues.length}건${project.more ? " 이상" : ""}`;
}

function Team({
  answer,
  jira,
  byId,
  onOpen,
}: {
  answer: SinceLastMeeting;
  jira: JiraProjectIssues[];
  byId: Map<string, ActionItemRead>;
  onOpen: (id: string) => void;
}) {
  if (answer.state === "no_meeting") {
    return (
      <div className="mb-4">
        <h3 className="mb-1 text-[var(--color-ink-muted)]" style={meta}>
          {answer.team_name}
        </h3>
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          분석을 마친 회의가 아직 없습니다. 회의가 한 번 분석되면 그 뒤로 바뀐 할 일이 이곳에
          보입니다.
        </p>
      </div>
    );
  }
  const parts = SINCE_PARTS.map((part) => ({
    ...part,
    rows: answer[part.key].flatMap((id) => byId.get(id) ?? []),
  })).filter((part) => part.rows.length > 0);
  const held = answer.since ? heldOn(answer.since) : "";
  const counts = [`열린 할 일 ${answer.open}건`, jiraOpen(jira, answer.team_id)].filter(Boolean);

  return (
    <div className="mb-4">
      <h3 className="mb-1 text-[var(--color-ink-muted)]" style={meta}>
        {answer.team_name} · 지난 회의: {answer.meeting_title}
        {held ? ` (${held})` : ""}
      </h3>
      <p className="mb-2 text-[var(--color-ink-body)]" style={meta}>
        {counts.join(" · ")}
      </p>
      {parts.length === 0 ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          지난 회의 이후로 바뀐 할 일이 없습니다.
        </p>
      ) : (
        parts.map((part) => (
          <div key={part.key} className="mb-3">
            <h4 className="mb-1 text-[var(--color-ink-strong)]" style={meta}>
              {part.label} · {part.rows.length}건
            </h4>
            <ul className="border-t border-[var(--color-hairline)]">
              {part.rows.map((item) => (
                <Row key={item.id} item={item} onOpen={onOpen} />
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}

function Row({ item, onOpen }: { item: ActionItemRead; onOpen: (id: string) => void }) {
  return (
    <li
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-[var(--color-hairline)]"
      style={{ paddingBlock: "var(--space-8)" }}
    >
      <button
        type="button"
        className="min-w-0 flex-1 text-left text-[var(--color-ink-strong)]"
        style={body}
        onClick={() => onOpen(item.id)}
      >
        {rowTitle(item.title, item.description).shown}
      </button>
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        {item.due_date ? shownDue(item.due_date) : "기한 없음"}
      </span>
    </li>
  );
}
