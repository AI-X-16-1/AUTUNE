import type { StatusVariant } from "@/shared/ui/StatusDot";

import type { MeetingStatus } from "./types";

/**
 * What a meeting's status is called, and which dot it gets.
 *
 * One map for the whole feature. The home list and the meeting header name the
 * same seven states, and they were two copies waiting to disagree — a label
 * renamed in one place and not the other is the row and the heading telling a
 * person two different things about one meeting. Same argument the backend's
 * `require_team_member` docstring makes about an authorisation check in two
 * places.
 *
 * Keyed on `MeetingStatus`, not `string`, so the `ck_meetings_status`
 * constraint and this map cannot drift: a status added to the backend fails
 * `tsc` here until it is named.
 */
export const STATUS_LABEL: Record<MeetingStatus, string> = {
  scheduled: "예정",
  recording: "녹음 중",
  analyzing: "분석 중",
  awaiting_confirmation: "확인 대기",
  complete: "분석 완료",
  delivered: "전달됨",
  failed: "실패",
};

/**
 * The dot beside the label. `hollow` is the ui-spec's ring: anything pending.
 *
 * `failed` is the only `critical` — red belongs to failure and to elapsing
 * time, nothing else (`Button`). `awaiting_confirmation` is `attention`
 * because somebody has to do something; `complete` and `delivered` are both
 * `confirmed` because from this screen they are the same good outcome, and the
 * label is what tells them apart.
 */
export const STATUS_DOT: Record<
  MeetingStatus,
  { variant: StatusVariant; hollow?: boolean }
> = {
  scheduled: { variant: "idle", hollow: true },
  recording: { variant: "progress" },
  analyzing: { variant: "progress" },
  awaiting_confirmation: { variant: "attention" },
  complete: { variant: "confirmed" },
  delivered: { variant: "confirmed" },
  failed: { variant: "critical" },
};

/**
 * Whether this meeting's microphone is still open, which is what decides where
 * a list row points.
 *
 * A meeting being recorded belongs at `/meetings/{id}/live`. `/meetings/{id}`
 * draws the pipeline stages, and a meeting still `recording` has not reached
 * them, so somebody clicking it mid-meeting would land on a screen with nothing
 * to say. Every other status goes to the stored meeting, which decides for
 * itself whether to draw stages or a transcript.
 *
 * A predicate rather than a function returning a path: Next's typed routes are
 * template-literal types, and a helper that returns `string` is not one of
 * them. The caller writes the two literals and `tsc` checks both.
 */
export function isBeingRecorded(status: MeetingStatus): boolean {
  return status === "recording";
}
