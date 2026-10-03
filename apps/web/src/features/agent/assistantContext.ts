/**
 * What the assistant knows about the page it was opened on (S34 section 4):
 * the header's context label, the meeting a run is bound to, and the
 * suggested questions.
 *
 * The suggestions differ from the design's table on purpose. Today a chat
 * turn goes to one subagent or to none (agent-assistant.md section 9, item 2),
 * so every chip here is a question a subagent answers. None of them writes:
 * a chip runs on one click, and asking Report for a draft is an L1 write
 * (#651 review), so that one is typed, not offered. A chip that always
 * came back "답할 수 없습니다" would teach people the assistant does not work.
 */

export type AssistantContext = {
  /** "{label} 보고 있음" in the header. */
  label: string;
  /** Sent with each turn; the run is bound to this meeting. */
  meetingId?: string;
  suggestions: string[];
};

const MEETING = /^\/meetings\/(mtg_[A-Za-z0-9]+)(?:\/|$)/;

const TEAM_QUESTIONS = [
  "업무가 한 사람에게 몰려 있어?",
  "액션아이템을 다시 나눠 볼까?",
];

export function contextFor(pathname: string): AssistantContext {
  const meeting = MEETING.exec(pathname);
  if (meeting) {
    return {
      // Until GET /api/agent/meeting-label answers with the title (Assistant).
      label: "회의",
      meetingId: meeting[1],
      suggestions: [
        "후속 회의가 필요할까?",
        "이 회의에서 확인 못 한 것 조사해 줘",
      ],
    };
  }
  if (pathname.startsWith("/actions"))
    return { label: "액션아이템", suggestions: TEAM_QUESTIONS };
  if (pathname.startsWith("/decisions"))
    return { label: "결정 계보", suggestions: TEAM_QUESTIONS };
  if (pathname.startsWith("/dashboard"))
    return { label: "대시보드", suggestions: TEAM_QUESTIONS };
  if (pathname.startsWith("/approvals"))
    return { label: "승인 대기", suggestions: TEAM_QUESTIONS };
  if (pathname.startsWith("/settings"))
    return { label: "설정", suggestions: TEAM_QUESTIONS };
  return { label: "홈", suggestions: TEAM_QUESTIONS };
}
