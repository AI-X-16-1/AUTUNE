import type { SinceLastMeeting } from "./api";

/** The answer's keys that are lists of item ids. */
type PartKey = {
  [Key in keyof SinceLastMeeting]: SinceLastMeeting[Key] extends string[] ? Key : never;
}[keyof SinceLastMeeting];

/**
 * The parts of "지난 회의 이후", in the order a team goes through them at the
 * start of a meeting: what is over first, then what needs a word, then what
 * is new. One list for the board's section and the agenda source, so the two
 * name and order the same answer the same way.
 *
 * 끝내지 않고 닫힌 일 is its own part and not under 끝난 일: a closed item is
 * done on the board and is not work somebody finished (#856).
 */
export const SINCE_PARTS: readonly { key: PartKey; label: string }[] = [
  { key: "finished", label: "끝난 일" },
  { key: "closed", label: "끝내지 않고 닫힌 일" },
  { key: "late", label: "늦은 일" },
  { key: "started", label: "시작한 일" },
  { key: "created", label: "새로 생긴 일" },
];
