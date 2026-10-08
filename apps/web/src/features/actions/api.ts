/** Calls to /api/extraction. This feature calls no other module's endpoints. */
import { scopeQuery, type IntegrationScope } from "@/shared/api/auth";
import { api } from "@/shared/api/client";

import type {
  ActionItemDetail,
  DecisionDetail,
  ActionItemRead,
  ActionStatus,
  CarriedOver,
  DecisionStatus,
  ExtractionResult,
  Material,
  MeetingReview,
  MeetingSummary,
  ReviewDecision,
  ConfirmationAnswer,
  MyConfirmation,
  Project,
  ProjectDraft,
  ProjectSendReport,
  TeamName,
  SendTarget,
} from "./types";

export { api };

/** Everything this meeting produced: classifications, items, decisions. */
export const getResults = (meetingId: string) =>
  api.extraction<ExtractionResult>(`/results/${meetingId}`);

/**
 * Every field optional; the server ANDs the ones given. `due_before` is strict —
 * today's date asks for what is overdue, and an undated item never matches it.
 */
export interface ActionItemFilter {
  meeting_id?: string;
  assignee_id?: string;
  status?: ActionStatus;
  due_before?: string;
}

export const listActionItems = (filter: ActionItemFilter = {}) => {
  const query = new URLSearchParams(
    Object.entries(filter).filter(([, value]) => value !== undefined) as [string, string][],
  ).toString();
  return api.extraction<ActionItemRead[]>(`/action-items${query ? `?${query}` : ""}`);
};

/**
 * One item with the words of the utterances it came from, for the drawer.
 *
 * The only call in this feature that brings utterances back verbatim, and it
 * asks for one item's at a time — the list carries their ids only. Call it when
 * a quotation is about to be shown, not to prefetch a board.
 */
export const getActionItem = (id: string) =>
  api.extraction<ActionItemDetail>(`/action-items/${encodeURIComponent(id)}`);

/**
 * One decision with the words of the utterances it was settled in. As with
 * `getActionItem`, the list never carries them — ask for a row's when it is on
 * screen.
 */
export const getDecision = (id: string) =>
  api.extraction<DecisionDetail>(`/decisions/${encodeURIComponent(id)}`);

export interface ActionItemDraft {
  meeting_id: string;
  description: string;
  assignee_id?: string | null;
  assignee_label?: string | null;
  due_date?: string | null;
  source_utterance_ids?: string[];
}

/**
 * Add an item the model missed.
 *
 * ADR 0006 ranks recall above precision because a wrong item costs a click and
 * a missing one costs re-reading the meeting — but recall alone does not close
 * that gap, since the user still has to notice the absence. This is the path
 * that lets them fix it, and without it the decision does not hold.
 */
export const createActionItem = (draft: ActionItemDraft) =>
  api.extraction<ActionItemRead>("/action-items", {
    method: "POST",
    body: JSON.stringify(draft),
  });

/** A member of the meeting's team, as the assignee picker offers them. */
export interface Assignable {
  user_id: string;
  name: string;
}

/**
 * Who an item of this meeting can be assigned to. From module B's own
 * route: this feature calls `/api/extraction` only, and the same people
 * under `/api/audio` belong to the transcript feature.
 */
export const listAssignable = (meetingId: string) =>
  api.extraction<Assignable[]>(`/meetings/${encodeURIComponent(meetingId)}/assignable`);

export const updateActionItem = (id: string, changes: Partial<ActionItemDraft & { status: ActionStatus }>) =>
  api.extraction<ActionItemRead>(`/action-items/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });

/**
 * Remove an item the model got wrong. **The row is gone, not flagged.**
 *
 * `docs/architecture/privacy.md` allows no soft deletes and no tombstones
 * holding content. Nothing in this feature may offer an undo that restores the
 * text, or a trash view, or a "deleted" filter — there is nothing left to show.
 * The server records that a deletion happened, which is all the edit-cost metric
 * asks for.
 *
 * The server answers 204 with no body, and `request` parses every 2xx body as
 * JSON, so an empty 204 threw a `SyntaxError` after the row was already gone —
 * the board kept the card, and once the drawer reported failures it said the
 * delete had failed. An error status still arrives as `ApiError`; only the parse
 * of an empty success is dropped (`withoutBody`). The shared client is the
 * better place for this (all five own it); until then it stays in this feature.
 */
export const deleteActionItem = (id: string) =>
  withoutBody(`/action-items/${encodeURIComponent(id)}`);

/** What a bulk confirm or delete did, id by id. */
export interface BulkActionResult {
  confirmed: string[];
  deleted: string[];
  /** Unknown, another team's, or no longer in 확인 필요. */
  skipped: string[];
}

/**
 * Confirm or delete several 확인 필요 items at once (the user, 2026-10-04).
 * Each goes the way a single one does: a confirmation is recorded and sends
 * the item's copies; a deletion closes them first.
 */
export const bulkActionItems = (ids: string[], action: "confirm" | "delete") =>
  api.extraction<BulkActionResult>("/action-items/bulk", {
    method: "POST",
    body: JSON.stringify({ ids, action }),
  });

/** A request answered 204 (a `DELETE` unless told otherwise): an empty
 * success is not a parse failure. */
async function withoutBody(
  path: string,
  init: RequestInit = { method: "DELETE" },
): Promise<void> {
  try {
    await api.extraction<void>(path, init);
  } catch (cause) {
    if (cause instanceof SyntaxError) return;
    throw cause;
  }
}

/**
 * What the team's earlier meetings left open, for the popup a review opens with
 * (WBS 4.8). Counts cover everything; `items` is the most urgent ten.
 */
export const getCarriedOver = (meetingId: string) =>
  api.extraction<CarriedOver>(`/carried-over/${encodeURIComponent(meetingId)}`);

/** The reader's own ambiguous agreements in this meeting, to answer here (#585). */
export const getMyConfirmations = (meetingId: string) =>
  api.extraction<MyConfirmation[]>(`/confirmations?meeting_id=${encodeURIComponent(meetingId)}`);

/** Answer one of them -- the DM button's path; a commitment makes a draft. */
export const answerConfirmation = (utteranceId: string, answer: ConfirmationAnswer) =>
  api.extraction<MyConfirmation>(`/confirmations/${encodeURIComponent(utteranceId)}`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });

/** S15's 요약 tab (#421): the meeting's decisions, items, counts and memo. */
export const getSummary = (meetingId: string) =>
  api.extraction<MeetingSummary>(`/summary/${encodeURIComponent(meetingId)}`);

/** Replace the team's memo; a blank one removes it. Answers with the summary. */
export const putSummaryNote = (meetingId: string, body: string) =>
  api.extraction<MeetingSummary>(`/summary/${encodeURIComponent(meetingId)}/note`, {
    method: "PUT",
    body: JSON.stringify({ body }),
  });

/** The team's projects, named by one of its meetings or by the team. */
export const listProjects = (scope: IntegrationScope) =>
  api.extraction<Project[]>(`/projects?${scopeQuery(scope)}`);

/** Words said often in the team's meetings that no project has yet. */
export const listProjectSuggestions = (teamId: string) =>
  api.extraction<{ word: string; count: number }[]>(
    `/projects/suggestions?team_id=${encodeURIComponent(teamId)}`,
  );

/** Every project of every team the reader is on, for the board across meetings. */
export const listMyProjects = () => api.extraction<Project[]>("/projects/mine");

/** The reader's own teams by name, for the board across meetings. */
export const listMyTeams = () => api.extraction<TeamName[]>("/teams/mine");

export const createProject = (teamId: string, draft: ProjectDraft) =>
  api.extraction<Project>(`/projects?team_id=${encodeURIComponent(teamId)}`, {
    method: "POST",
    body: JSON.stringify(draft),
  });

export const updateProject = (teamId: string, id: string, draft: ProjectDraft) =>
  api.extraction<Project>(
    `/projects/${encodeURIComponent(id)}?team_id=${encodeURIComponent(teamId)}`,
    { method: "PUT", body: JSON.stringify(draft) },
  );

/** Delete a project; what was in it becomes 미분류. */
export const deleteProject = (teamId: string, id: string) =>
  withoutBody(`/projects/${encodeURIComponent(id)}?team_id=${encodeURIComponent(teamId)}`);

/** The Drive files the team keeps on its 자료 screen, the newest first (#817). */
export const listMaterials = (teamId: string) =>
  api.extraction<Material[]>(`/materials?team_id=${encodeURIComponent(teamId)}`);

/**
 * Put a Drive file on the team's shelf. The link goes as pasted and the
 * server keeps only the file's id; 409 when the team already keeps the file,
 * 422 when the link names no Drive file or the title is blank.
 */
export const registerMaterial = (teamId: string, draft: { title: string; link: string }) =>
  api.extraction<Material>(`/materials?team_id=${encodeURIComponent(teamId)}`, {
    method: "POST",
    body: JSON.stringify(draft),
  });

/** Take a material off the team's shelf. The Drive file is not touched. */
export const deleteMaterial = (teamId: string, id: string) =>
  withoutBody(`/materials/${encodeURIComponent(id)}?team_id=${encodeURIComponent(teamId)}`);

/** Put an item in one of its team's projects, or none (`null`). */
export const placeActionItem = (id: string, projectId: string | null) =>
  api.extraction<ActionItemRead>(`/action-items/${encodeURIComponent(id)}/project`, {
    method: "PUT",
    body: JSON.stringify({ project_id: projectId }),
  });

/** Put a decision in one of its team's projects, or none. Answered 204. */
export const placeDecision = (id: string, projectId: string | null) =>
  withoutBody(`/decisions/${encodeURIComponent(id)}/project`, {
    method: "PUT",
    body: JSON.stringify({ project_id: projectId }),
  });

/**
 * Send each project's confirmed decisions and items, as "팀-프로젝트-날짜", to
 * the chosen tools. Sending again updates the same copies.
 */
export const sendSummaryProjects = (meetingId: string, targets: SendTarget[]) =>
  api.extraction<ProjectSendReport>(
    `/summary/${encodeURIComponent(meetingId)}/projects/send`,
    { method: "POST", body: JSON.stringify({ targets }) },
  );

/** Place the meeting's rows in the team's projects again, by the rules. */
export const assignSummaryProjects = (meetingId: string) =>
  api.extraction<MeetingSummary>(
    `/summary/${encodeURIComponent(meetingId)}/projects/assign`,
    { method: "POST" },
  );

/** The caller's own due-date reminders by Slack DM (review of #751). */
export interface DueReminderSetting {
  /** On unless the caller turned them off. */
  on: boolean;
  /** Whether this server sends the due-date reminder at all. */
  sent_here: boolean;
  /** Whether it sends the Monday digest, which the same switch governs. */
  weekly_here: boolean;
  /** Whether it sends the morning DM, which the same switch governs. */
  daily_here: boolean;
  /** The work-report draft (2026-10-07); absent from an older server. */
  work_report_here?: boolean;
  /** The notice after a meeting (2026-10-07); absent from an older server. */
  after_meeting_here?: boolean;
}

export const getDueReminders = () => api.extraction<DueReminderSetting>("/me/due-reminders");

/** Only the caller's own: the request names nobody. */
export const setDueReminders = (on: boolean) =>
  api.extraction<DueReminderSetting>("/me/due-reminders", {
    method: "PUT",
    body: JSON.stringify({ on }),
  });

/**
 * The caller's own leave dates (`YYYY-MM-DD`, both included); both null is no
 * pause. `on_calendar` is the person's tick on "내 Google 캘린더에도 추가": sent
 * true, the range also goes onto their own calendar as one private all-day
 * event; sent false, an event put there for an earlier range is removed; left
 * out, the calendar stays as it stands -- an event there moves with the dates
 * and none is made where there is none. Answered true while such an event
 * stands.
 */
export interface NotificationPause {
  starts_on: string | null;
  ends_on: string | null;
  on_calendar?: boolean;
}

/** What happened on the person's calendar for the save just made. */
export type LeaveCalendarOutcome =
  | "off"
  | "added"
  | "removed"
  | "removal_queued"
  | "not_connected"
  | "not_removed"
  | "failed";

/**
 * The pause as it stands; whether this server also reads out-of-office time
 * from a connected calendar; whether the caller has a calendar connected (the
 * box is drawn only then); and, on the answer to a save, what happened there.
 */
export interface NotificationPauseRead extends NotificationPause {
  calendar_leave?: boolean;
  calendar_connected?: boolean;
  calendar?: LeaveCalendarOutcome | null;
}

export const getNotificationPause = () =>
  api.extraction<NotificationPauseRead>("/me/notification-pause");

/** Only the caller's own: the request names nobody. Both null clears it. */
export const setNotificationPause = (pause: NotificationPause) =>
  api.extraction<NotificationPauseRead>("/me/notification-pause", {
    method: "PUT",
    body: JSON.stringify(pause),
  });

/** Everything in one meeting that needs a person before it goes anywhere (#246). */
export const getReview = (meetingId: string) =>
  api.extraction<MeetingReview>(`/reviews/${encodeURIComponent(meetingId)}`);

/**
 * Confirm, reject or reword one decision. `pending` undoes a mis-click; sending
 * the model's own wording back clears a rewording.
 */
export const reviewDecision = (
  id: string,
  changes: { status?: DecisionStatus; statement?: string },
) =>
  api.extraction<ReviewDecision>(`/decisions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });

/** A decision the model missed. Confirmed from the moment it exists. */
export const createDecision = (meetingId: string, statement: string) =>
  api.extraction<ReviewDecision>("/decisions", {
    method: "POST",
    body: JSON.stringify({ meeting_id: meetingId, statement }),
  });

/**
 * Delete a decision. A person's is gone; a model's is rejected instead, so the
 * next run cannot propose it again — the server decides which.
 */
export const deleteDecision = (id: string) => withoutBody(`/decisions/${encodeURIComponent(id)}`);

/**
 * Send one item to the team's connected tools again -- "다시 시도" beside a
 * failed copy (#680). Answers before the sync runs: `queued` is false for an
 * item that was never confirmed, which has nothing outside to retry.
 */
export const retrySync = (id: string) =>
  api.extraction<{ queued: boolean }>(`/action-items/${encodeURIComponent(id)}/sync`, {
    method: "POST",
  });

/**
 * What became of a meeting's extraction: when it last went through, how many
 * runs in a row failed since, whether the server is still trying by itself,
 * and whether a "다시 추출" is waiting for the worker. Counts and times only.
 *
 * The last three are why a board can be empty with nothing wrong on record:
 * the first run is not in yet, it never came, or it was allowed to read none
 * of the meeting's lines.
 */
export interface ExtractionState {
  extracted_at: string | null;
  failures: number;
  failed_at: string | null;
  will_retry: boolean;
  /**
   * The last failure was passing a stored result on to the other analyses:
   * the board's items and decisions are this run's, so "could not extract"
   * would be false.
   */
  not_published: boolean;
  /**
   * The last run stored its rows and could not read part of the transcript:
   * the board's rows are this run's and some may be missing. Counted in
   * `failures`, so the worker tries again.
   */
  partly_unread: boolean;
  requested: boolean;
  requested_at: string | null;
  /** A transcript is stored and its first run has left nothing yet. */
  in_progress: boolean;
  /** The same, half an hour on: the run did not come. */
  overdue: boolean;
  /** The last run read no line: no speech in the meeting has consent on record. */
  read_nothing: boolean;
}

export const getExtractionState = (meetingId: string) =>
  api.extraction<ExtractionState>(`/meetings/${encodeURIComponent(meetingId)}/extraction`);

/**
 * Extract this meeting's action items and decisions again. Accepted, not
 * done: the worker runs it within a minute. 409 for a meeting with no
 * transcript yet, 429 for a second request right after the first.
 */
export const requestExtraction = (meetingId: string) =>
  api.extraction<ExtractionState>(`/meetings/${encodeURIComponent(meetingId)}/extraction`, {
    method: "POST",
  });

/** Re-push this meeting's items to Notion. */
export const syncResults = (meetingId: string) =>
  api.extraction<void>(`/results/${meetingId}/sync`, { method: "POST" });

/**
 * Put every confirmed item of the meeting's team into its Jira project -- right
 * after a project is chosen, so a project replacing a deleted one holds
 * everything (#458).
 */
export const backfillJira = (scope: IntegrationScope) =>
  api.extraction<{ synced: number; failed: number }>(
    `/jira/backfill?${scopeQuery(scope)}`,
    { method: "POST" },
  );

export interface JiraIssue {
  key: string;
  summary: string;
  status: string | null;
  status_category: string | null;
  assignee: string | null;
  due_date: string | null;
  /** The issue in the team's Jira, or null when the server could not build a safe link. */
  url: string | null;
  /** True for an issue Autune made from an action item. */
  from_autune: boolean;
}

export interface JiraProjectIssues {
  team_id: string;
  team_name: string;
  project_key: string | null;
  state: "ok" | "no_project" | "needs_reconnect" | "unavailable";
  issues: JiraIssue[];
  /** Jira has more open issues than were read. */
  more: boolean;
}

/**
 * The open issues of the Jira projects the caller's teams connected, read from
 * Jira by the server at this moment and stored nowhere. A team that never
 * connected Jira is not in the answer.
 */
export const listJiraOpenIssues = () =>
  api.extraction<JiraProjectIssues[]>("/jira/issues");

export interface NotionPage {
  id: string;
  title: string;
}

export interface NotionSetupState {
  connected: boolean;
  /** Notion refused the stored token: the team connects again. */
  needs_reconnect?: boolean;
  pages?: NotionPage[];
  target?: { parent_page_id: string; action_db_url: string; decision_db_url: string; minutes_db_url: string } | null;
}

export interface NotionSetupResult {
  databases: "created" | "added" | "reused";
  action_db_url: string;
  decision_db_url: string;
  minutes_db_url: string;
  /** Confirmed items and decisions go in from the worker, not this request (#481). */
  backfill: "queued";
}

/** The pages the team shared with Autune, and where its databases are (#428). */
export const getNotionSetup = (scope: IntegrationScope) =>
  api.extraction<NotionSetupState>(`/notion/setup?${scopeQuery(scope)}`);

/**
 * Make the databases under `pageId` -- or, without one, in an "Autune" page
 * among the person's private Notion pages -- and queue filling them with
 * everything confirmed.
 */
export const setUpNotion = (scope: IntegrationScope, pageId?: string) =>
  api.extraction<NotionSetupResult>(
    `/notion/setup?${scopeQuery(scope)}` +
      (pageId ? `&page_id=${encodeURIComponent(pageId)}` : ""),
    { method: "POST" },
  );

