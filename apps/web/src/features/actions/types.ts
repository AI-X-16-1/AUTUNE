/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type {
  ActionItem,
  ActionStatus,
  ExternalRef,
  ExtractionResult,
} from "@autune/contracts";

import type { ActionItem, ActionStatus } from "@autune/contracts";

/**
 * The four columns of the action board, left to right (S17).
 *
 * The order is the order the work moves in, and it is the same list as
 * `ActionStatus` — the columns *are* the statuses, so a status added to the
 * contract shows up here as a type error rather than as a missing column.
 *
 * `needs_confirmation` is Autune-only: no external issue exists for it yet.
 */
export const COLUMNS = [
  "needs_confirmation",
  "todo",
  "in_progress",
  "done",
] as const satisfies readonly ActionStatus[];

export const COLUMN_LABELS: Record<ActionStatus, string> = {
  needs_confirmation: "확인 필요",
  todo: "진행 전",
  in_progress: "진행 중",
  done: "완료",
};

/**
 * What a document -- the 요약 tab, the copied minutes -- says after a decision
 * nobody has confirmed. Where the line came from, not what it waits for: on a
 * page of minutes "…로 결정함 (확인 대기)" read as if the decision itself were
 * not final, or as a workflow that had stalled (the user, 2026-10-08). The
 * review list, where a person acts, says "확인 필요" as the board does.
 */
export const UNCONFIRMED_DECISION = "자동 추출";

/**
 * Where one item stands with one outside system — `ExternalRefRead`.
 *
 * Not the generated `ExternalRef` from `@autune/contracts`: that type is the
 * outbound event to D and E and requires `url` because it is only ever built
 * for a ref that finished. This reads the other two states a sync can be in:
 * `url` is `null` while claimed but not yet sent, or failed; no entry at all
 * (this system absent from the array) means nothing has tried yet.
 */
export interface ExternalRefRead {
  system: "notion" | "jira"; // the systems ext_external_refs holds (#650)
  url: string | null;
  external_id: string | null;
}

/**
 * The last attempt to copy the item to `system` failed (#680). A kind and a
 * time are all the server keeps: no message from the outside service.
 */
export interface SyncFailure {
  system: "notion" | "jira" | "calendar";
  kind: "privacy" | "reconnect" | "unreachable" | "rejected";
  failed_at: string;
}

/**
 * What this reader may be told about the item and its assignee's calendar.
 * The four reasons about the item go to any reader; `sent`, a bare `none` and
 * `not_connected` are about one person's calendar and go only to the assignee.
 */
export interface CalendarState {
  state: "sent" | "none";
  reason:
    | "not_confirmed"
    | "no_due_date"
    | "no_account"
    | "not_on_team"
    | "not_connected"
    | null;
}

/**
 * One item as `/api/extraction` returns it — `ActionItemRead` in
 * `modules/extraction/src/autune_extraction/schemas.py`.
 *
 * **Not a contract, so it is not generated.** The contracts package covers what
 * crosses between modules; this is module B's own response body, which nobody
 * else parses, and no generator reads it. It extends the generated `ActionItem`
 * so every field the two share stays generated, and names only the three the
 * server adds. `test_the_web_read_model_mirror_is_current` in
 * `modules/extraction/tests` pins the Python side's field set and names this
 * file, so a field added there fails a test rather than going missing here.
 */
export interface ActionItemRead extends ActionItem {
  meeting_id: string;
  /** The meeting the item came from; the board across meetings shows it. */
  meeting_title?: string | null;
  /**
   * The team whose meeting the item came from. The board across meetings shows
   * the items team by team with it; the names come from `listMyTeams`.
   */
  team_id?: string | null;
  /**
   * `model` for what the pipeline drafted, `user` for what a person typed,
   * `followup` for the Follow-up agent's "후속 회의 잡기" (#561), `chat` for an
   * item drafted from an utterance in the chat.
   */
  origin: "model" | "user" | "followup" | "chat";
  /** The team's project this item is about, or null for none (미분류). */
  project_id?: string | null;
  /**
   * For an open item: how many of its team's meetings were held since it was
   * made. `STALE_AFTER` (3) or more reads as stuck.
   */
  carried_meetings?: number;
  /**
   * A line it was drawn from was corrected after it was made (#586) and the
   * text may still need a person's eye. Cleared by their next edit.
   */
  needs_recheck: boolean;
  /**
   * A done item that was closed without being finished (#856). The card says
   * 닫힘 so 완료 does not show it as work somebody finished. About the item:
   * who closed it is not kept.
   */
  closed_unfinished?: boolean;
  /**
   * Whether the item belongs in the candidate band. Decided by the server,
   * which holds the threshold the classifier's confidences are measured
   * against; false for everything while that threshold is unset (#122).
   */
  is_candidate: boolean;
  /**
   * At most `notion` today (#30). `jira` was designed (ui-spec S18, S28) but
   * dropped before being built (#82). Not `external_refs`: the generated
   * `ActionItem` already has a field by that name (`ExternalRef[]`, `url`
   * required, the outbound-only shape), and `extends` cannot narrow it to
   * this stricter one.
   */
  sync_refs: ExternalRefRead[];
  /**
   * Systems whose last copy of this item failed (#680). A `calendar` failure
   * is sent only to the item's assignee. Empty when none did.
   */
  sync_failures?: SyncFailure[];
  /**
   * A one-line preview of what the item was made from, beyond `description`
   * itself: the words as said, cut to the part the item is about when the run
   * recorded one. Rule-based, not a model: the longest source, truncated.
   * `null` when `description` already is that line (one source, no model's
   * sentence); the card falls back to the source count.
   */
  summary: string | null;
  /**
   * The assignee's current display name, read fresh — never stored.
   * `assignee_label` is only "the name as spoken, kept when it does not
   * resolve to an account"; an identified assignee has no label at all, so a
   * card reading only `assignee_label` shows an assigned item as unassigned.
   * `null` until `assignee_id` resolves to an account that still exists.
   */
  assignee_name: string | null;
  /**
   * Whether `description` is a resolver's rewrite of the source utterance
   * rather than the utterance verbatim (#175, #366). S18 shows this so a
   * reviewer knows which descriptions are the speaker's own words and which
   * are a model's paraphrase — worth a closer look, since a paraphrase can be
   * wrong in ways a verbatim quote cannot. Defaults `false`: a hand-added
   * item, a raw quote, or a resolution that fell back to one all read the
   * same as "not resolved".
   */
  description_resolved: boolean;
  /**
   * The phrase `due_date` was parsed from ("다음 주 화요일", "9/20") — S18
   * shows both, so a person can judge the parse instead of taking the
   * resolved date on faith. `null` for a hand-added item, or a model item
   * where no date phrase was said at all.
   */
  due_text: string | null;
  /**
   * How many of the item's sources were deleted after it was made (ADR 0007).
   * `source_utterance_ids` lists only the ones that still exist, so a model
   * item whose transcript went has an empty list — the same shape as a
   * hand-added one. Tell them apart by `origin`; this says the evidence is gone.
   */
  deleted_source_count: number;
  /**
   * An open item whose assignee is no longer on the meeting's team (ADR 0007).
   * `assignee_id` and `assignee_name` are already `null` when the assignee is
   * not a member; this marks the ones someone has to pick up. S17 puts them at
   * the top of their column.
   */
  needs_reassignment: boolean;
}

/** One source utterance's words, already masked by module A. */
export interface SourceUtterance {
  id: string;
  text: string;
  /**
   * The part of `text` the item or decision was made from, cut from it as
   * stored — never reworded. Absent or null when it was made from the whole
   * utterance, for a row older than this field, and for a context line.
   */
  excerpt?: string | null;
}

/**
 * One item and its evidence — `ActionItemDetail`, from
 * `GET /action-items/{id}`. The only response that carries utterances verbatim.
 */
/**
 * One thing a person did to an item (S18, #109): which fields, when. Never
 * the value before or after, and never who.
 */
export interface EditHistoryEntry {
  /** `closed`: closed without being finished — not a correction of the item. */
  kind: "created" | "edited" | "closed";
  /** For `edited`: e.g. `["due_date"]`. Empty for `created`, `closed` and old edits. */
  fields: string[];
  at: string;
}

export interface ActionItemDetail extends ActionItemRead {
  /** In the order they were spoken. */
  sources: SourceUtterance[];
  /**
   * What was said just before the first source, in spoken order — so a sentence
   * with nothing to point at ("다음 주까지 볼게요") reads with what it is about.
   * Not what the item was drawn from.
   */
  context?: SourceUtterance[];
  /**
   * The lines the summary says it was written from, beyond the commitment itself
   * (`ext_action_item_related`), in spoken order — shown beneath the summary so a
   * person can check the sentence against them and correct it.
   */
  related?: SourceUtterance[];
  /** Oldest first. Empty for an item the model extracted and nobody touched. */
  history?: EditHistoryEntry[];
  /** Where the item stands on its assignee's calendar, and why it has no event. */
  calendar?: CalendarState | null;
  /**
   * The reader's own Slack confirmation DM about one of this item's lines —
   * only for the person it went to, since nobody else can open it (#680).
   */
  confirmation_dm_url?: string | null;
}

/** `CarriedOverItem`: an open item from an earlier meeting of the same team. */
export interface CarriedOverItem extends ActionItemRead {
  meeting_title: string;
  meeting_started_at: string | null;
}

/**
 * `CarriedOver` (`GET /carried-over/{meeting_id}`, WBS 4.8). `open` and
 * `overdue` count everything; `items` is the most urgent part, overdue first.
 */
export interface CarriedOver {
  open: number;
  overdue: number;
  /** Open items carried through three or more meetings. */
  stale?: number;
  items: CarriedOverItem[];
}

/**
 * An item the model was unsure about.
 *
 * The server says so. This used to compare `confidence` against a 0.5 the
 * client held, which was one deploy away from disagreeing with the server about
 * which items the meeting produced — and the number was a placeholder nobody
 * had measured.
 */
export function isCandidate(item: ActionItemRead): boolean {
  return item.is_candidate;
}

/** `SummaryDecision`: a decision as the summary tab lists it. */
export interface SummaryDecision {
  id: string;
  statement: string;
  status: "pending" | "confirmed";
  /** `ReviewDecision.summary`: what was said, beneath the statement. */
  summary?: string | null;
  /** The team's project it is about, or null for none (미분류). */
  project_id?: string | null;
}

/**
 * One of a team's projects (`ext_projects`, 2026-10-04). A meeting's decisions
 * and items point at one, by what was said or by a person, so a meeting that
 * covers several projects reads — and later goes out — project by project.
 */
export interface Project {
  id: string;
  /** The team it belongs to: what tells two teams' projects of one name apart. */
  team_id?: string;
  name: string;
  /** Other names people say for it, matched in what was said. */
  aliases: string[];
  jira_project_key: string | null;
}

/**
 * A Google Drive file a team keeps on its 자료 screen -- `MaterialRead` in
 * `modules/extraction/src/autune_extraction/schemas.py` (`ext_materials`,
 * #817). A title and which file it is: no address, which the screen builds
 * from the id (`@/shared/drive`), and no person.
 */
export interface Material {
  id: string;
  team_id: string;
  /** Typed by a member, shown as typed. */
  title: string;
  drive_file_id: string;
  drive_kind: "file" | "document" | "presentation" | "spreadsheets";
  /** ISO 8601, when it was registered. */
  created_at: string;
}

/**
 * One of the reader's teams, by name -- `TeamRead` in
 * `modules/extraction/src/autune_extraction/schemas.py`. The board across
 * meetings heads each team's items with it (`ActionItemRead.team_id`).
 */
export interface TeamName {
  id: string;
  name: string;
}

export type SendTarget = "notion" | "slack" | "jira" | "calendar";

/** What happened to each project's minutes in each tool. */
export interface ProjectSendReport {
  results: {
    project_id: string;
    project_name: string;
    target: SendTarget;
    outcome:
      | "created"
      | "updated"
      | "retracted"
      | "not_connected"
      | "no_date"
      | "failed";
  }[];
  /** Confirmed rows with no project, left out. */
  unsorted: number;
}

/** A project as a member types it. */
export interface ProjectDraft {
  name: string;
  aliases: string[];
  jira_project_key?: string | null;
}

/**
 * `MeetingSummary` (`GET /summary/{meeting_id}`, #421): S15's 요약 tab, v1 —
 * B's rows in three levels and the team's memo. No model wrote any of it.
 */
/**
 * A summary of the whole meeting written by a cloud model (#421 v2). Only
 * when the deployment turned it on, and only while the lines it was written
 * from are unchanged.
 */
export interface GeneratedSummary {
  overview: string;
  points: string[];
  model_version: string;
  created_at: string;
}

export interface MeetingSummary {
  meeting_id: string;
  /** What the page of minutes is headed with: the meeting's title and start. */
  meeting_title?: string | null;
  meeting_started_at?: string | null;
  /** Confirmed first, then pending; a rejected decision is not listed. */
  decisions: SummaryDecision[];
  /** Every item of the meeting, whatever its status. */
  action_items: ActionItemRead[];
  open_questions: number;
  ambiguous_waiting: number;
  note: string | null;
  note_updated_at: string | null;
  /** v2: absent or null when no current summary is written. */
  generated?: GeneratedSummary | null;
  /** v2: none was written because the meeting is too long for one. */
  generated_too_long?: boolean;
  /** The team's projects, to group the decisions and items by. */
  projects?: Project[];
}

/** The memo's limit, the server's `MAX_NOTE_CHARS`. */
export const MAX_NOTE_CHARS = 2000;

/** Where a proposed decision stands with the people reviewing it (#246). */
export type DecisionStatus = "pending" | "confirmed" | "rejected";

/**
 * One decision as the review screen lists it — `ReviewDecision` in
 * `modules/extraction/src/autune_extraction/schemas.py` (#247).
 *
 * Module B's own response body, not a contract, so it is written here like
 * `ActionItemRead`. Unlike that one it is not pinned by a Python test yet: the
 * schema lives in #247, and the pin belongs in the same place once it is on
 * `main`.
 */
/**
 * Where one item or decision stands with one outside system —
 * `ExternalRefRead` in `modules/extraction/src/autune_extraction/schemas.py`.
 *
 * Not the generated `ExternalRef` from `@autune/contracts`: that type is the
 * outbound event to D and E and requires `url` because it is only ever built
 * for a ref that finished. This reads the other two states a sync can be in:
 * `url` is `null` while claimed but not yet sent, or failed; no entry at all
 * (this system absent from the array) means nothing has tried yet.
 */
export interface ExternalRefRead {
  system: "notion" | "jira"; // the systems ext_external_refs holds (#650)
  url: string | null;
  external_id: string | null;
}

export interface ReviewDecision {
  id: string;
  /** What will be sent: the person's rewording when there is one. */
  statement: string;
  /** What the model proposed, kept so the screen can show both. */
  model_statement: string;
  confidence: number;
  origin: "model" | "user";
  /** A source line was corrected since a person typed or reworded it (#586). */
  needs_recheck: boolean;
  status: DecisionStatus;
  /** Pre-check it? `null` while the candidate line is unset. */
  suggested: boolean | null;
  source_utterance_ids: string[];
  /**
   * Sources whose utterance was deleted since (#400) -- by a rerun of the
   * meeting or by a person deleting their own data. They are not in
   * `source_utterance_ids`. Absent from a server before the count existed.
   */
  deleted_source_count?: number;
  /**
   * A one-line preview of the sources, distinct from `statement` (which is
   * assembled or reworded). Rule-based, not a model: the longest source
   * utterance, truncated. `null` only when there are no sources at all.
   */
  summary: string | null;
  /**
   * At most `notion` today (#30). `jira` was designed (ui-spec S18, S28) but
   * dropped before being built (#82). Optional rather than required: the
   * backend only started sending this key
   * once #312 merged, and #314 (which declares this interface) landed first.
   * Absent means the same thing as `[]` -- the render side must not assume it.
   */
  sync_refs?: ExternalRefRead[];
}

/** One weak assent and where the speaker's DM stands. Read-only here. */
export interface ReviewAmbiguous {
  utterance_id: string;
  outcome: "not_asked" | "pending" | "undecided" | "resolved";
  resolved_kind: string | null;
}

/**
 * One decision and the words it was settled in — `DecisionDetail`, from
 * `GET /decisions/{id}`. The list carries ids and one preview line; the
 * verbatim quotations come one row at a time.
 */
export interface DecisionDetail extends ReviewDecision {
  /** In the order they were spoken: the proposal first, the settling turn last. */
  sources: SourceUtterance[];
  /** The lines just before the first source, in spoken order. */
  context?: SourceUtterance[];
  /** The lines the write-up says it used, beyond the turns it was settled in. */
  related?: SourceUtterance[];
}

/** `GET /reviews/{meeting_id}` — everything that needs a person first. */
export interface MeetingReview {
  meeting_id: string;
  decisions: ReviewDecision[];
  ambiguous_agreements: ReviewAmbiguous[];
  action_items: ActionItemRead[];
  pending_decisions: number;
}

/** What a speaker answers about one of their own ambiguous agreements. */
export type ConfirmationAnswer = "commitment" | "decision" | "not_commitment";

/**
 * `MyConfirmation` (`GET /confirmations?meeting_id=`, #585): an ambiguous
 * agreement the reader said, their own line as stored, and their answer if any.
 * Nobody else's questions ever come back.
 */
export interface MyConfirmation {
  utterance_id: string;
  text: string;
  answer: ConfirmationAnswer | null;
}
