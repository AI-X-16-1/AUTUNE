import type { PendingAction } from "./types";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/**
 * What an approval card calls its meeting (#854): the title and the day it
 * was held, in Korea's time ("주간 회의 · 10월 2일(목)"). Without a title the
 * card keeps the plain "회의 보기".
 */
export function meetingLabel(item: PendingAction): string {
  if (!item.meeting_title) return "회의 보기";
  if (!item.meeting_started_at) return item.meeting_title;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(new Date(item.meeting_started_at));
  const get = (type: string) =>
    Number(parts.find((p) => p.type === type)?.value);
  const year = get("year");
  const month = get("month");
  const day = get("day");
  const weekday =
    WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
  return `${item.meeting_title} · ${month}월 ${day}일(${weekday})`;
}
