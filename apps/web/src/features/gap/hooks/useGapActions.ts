"use client";

import { useCallback, useState } from "react";

import { ApiError } from "@/shared/api/client";

import {
  askGap,
  carryMeeting,
  chooseTemplate,
  dismissGap,
  editQuestion,
  sendCards,
  undoDismissGap,
} from "../api";
import type {
  AgendaOutcome,
  GapAsk,
  GapCardsSent,
  GapMeetingCarry,
  SlackOutcome,
} from "../types";

/** The second half of what "다음 회의 잡기" says, by what the calendar did. */
export const AGENDA_NOTICE: Record<AgendaOutcome, string | null> = {
  added: "다음 회의 일정 설명에 추가했습니다.",
  removed: "다음 회의 일정 설명에서 뺐습니다.",
  no_next_meeting: "예정된 다음 회의가 없어 캘린더에는 넣지 못했습니다.",
  no_event: "내 Google 캘린더에서 다음 회의 일정을 찾지 못했습니다.",
  external_attendees:
    "팀 밖 참석자가 있는 일정이라 캘린더에는 넣지 않았습니다. 일정 설명은 모든 참석자에게 보입니다.",
  hidden_attendees:
    "참석자 목록을 다 확인할 수 없는 일정이라 캘린더에는 넣지 않았습니다. 일정 설명은 모든 참석자에게 보입니다.",
  not_connected: "Google 캘린더가 연결되어 있지 않아 캘린더에는 넣지 못했습니다.",
  reconnect_required: "Google 캘린더를 다시 연결해야 합니다.",
  failed: "캘린더에 쓰지 못했습니다.",
  not_tried: null,
};

/** What the team's Slack channel did, after "다음 회의 잡기". */
export const AGENDA_SLACK_NOTICE: Record<SlackOutcome, string | null> = {
  posted: "팀 Slack 채널에 공지했습니다.",
  no_slack: "팀 Slack 채널이 연결되어 있지 않아 공지하지 못했습니다.",
  failed: "팀 Slack 채널에 공지하지 못했습니다.",
  refused: "개인정보로 보이는 내용이 있어 팀 Slack 채널에 공지하지 않았습니다.",
  not_tried: null,
};

/** What the screen says after "다음 회의 잡기". */
export function meetingCarryNotice(result: GapMeetingCarry): string {
  if (result.carried === 0) return "다음 회의로 넘길 갭이 없습니다.";
  const notes = [AGENDA_NOTICE[result.calendar], AGENDA_SLACK_NOTICE[result.slack]].filter(
    (note): note is string => note !== null,
  );
  return [`갭 ${result.carried}건을 다음 회의로 넘겼습니다.`, ...notes].join(" ");
}

/** What the screen says after "담당자 지정해 질문". */
export const ASK_NOTICE: Record<SlackOutcome, string> = {
  posted: "팀 Slack 채널에 담당자를 멘션해 질문을 올렸습니다.",
  no_slack: "팀 Slack 채널이 연결되어 있지 않아 질문을 보내지 못했습니다.",
  failed: "팀 Slack 채널에 질문을 올리지 못했습니다. 잠시 후 다시 시도해 주세요.",
  refused: "개인정보로 보이는 내용이 있어 팀 Slack 채널에 질문을 올리지 않았습니다.",
  not_tried: "질문을 보내지 않았습니다.",
};

/** What the screen says after "질문 카드 Slack 전송". */
export function cardsNotice(result: GapCardsSent): string {
  const before = result.sent > 0 ? `질문 카드 ${result.sent}건을 올린 뒤 ` : "";
  switch (result.slack) {
    case "posted": {
      const rest = result.high - result.sent;
      const more = rest > 0 ? ` 나머지 ${rest}건은 갭 리포트 링크로 안내했습니다.` : "";
      return `팀 Slack 채널에 질문 카드 ${result.sent}건을 올렸습니다.${more}`;
    }
    case "not_tried":
      return "Slack으로 보낼 위험도 높은 갭이 없습니다.";
    case "no_slack":
      return "팀 Slack 채널이 연결되어 있지 않아 질문 카드를 보내지 못했습니다.";
    case "refused":
      return `${before}개인정보로 보이는 내용이 있어 팀 Slack 채널에 더 보내지 않았습니다.`;
    case "failed":
      return `${before}팀 Slack 채널에 질문 카드를 올리지 못했습니다. 잠시 후 다시 시도해 주세요.`;
  }
}

/**
 * The writes S20 makes: dismissing a gap and taking that back, sending the
 * meeting's open gaps on to the next meeting and asking a member a gap's
 * question on the team's Slack channel or posting the open high gaps there as
 * question cards (#824), and holding the meeting to
 * another template.
 *
 * **Every write is followed by a read, never by a local edit.** The server
 * decides what a dismissal does to the report and the rail — the gap leaves
 * one and is marked on the other — and choosing a template re-runs the whole
 * comparison. Patching the screen's copy instead would be this feature
 * re-implementing those rules in a second place, which is how the two come to
 * disagree.
 *
 * `pending` names what is in flight (a gap id, `"template"` or `"agenda"`) so exactly that
 * control can show it. `notice` is what the last calendar write did — the mark
 * is set either way, so it is a note rather than a failure.
 */
export function useGapActions(reload: () => void) {
  const [pending, setPending] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** The question cards went out on this visit; the button is not offered again. */
  const [cardsSent, setCardsSent] = useState(false);

  const run = useCallback(
    async <T>(
      key: string,
      write: () => Promise<T>,
      message: string,
      say?: (result: T) => string | null,
    ) => {
      setPending(key);
      setFailure(null);
      setNotice(null);
      try {
        const result = await write();
        if (say) setNotice(say(result));
        reload();
      } catch {
        setFailure(message);
      } finally {
        setPending(null);
      }
    },
    [reload],
  );

  const dismiss = useCallback(
    (gapId: string) =>
      run(
        gapId,
        () => dismissGap(gapId),
        "갭을 해당 없음으로 표시하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      ),
    [run],
  );

  const undoDismiss = useCallback(
    (gapId: string) =>
      run(
        gapId,
        () => undoDismissGap(gapId),
        "해당 없음 표시를 되돌리지 못했습니다. 잠시 후 다시 시도해 주세요.",
      ),
    [run],
  );

  const scheduleNext = useCallback(
    (meetingId: string, eventId: string) =>
      run(
        "agenda",
        () => carryMeeting(meetingId, eventId),
        "갭을 다음 회의로 넘기지 못했습니다. 잠시 후 다시 시도해 주세요.",
        meetingCarryNotice,
      ),
    [run],
  );

  const ask = useCallback(
    (gapId: string, userId: string) =>
      run(
        gapId,
        () => askGap(gapId, userId),
        "질문을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
        (result: GapAsk) => ASK_NOTICE[result.slack],
      ),
    [run],
  );

  const sendToSlack = useCallback(
    (meetingId: string) =>
      run(
        "slack",
        () => sendCards(meetingId),
        "질문 카드를 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
        (result: GapCardsSent) => {
          if (result.slack === "posted") setCardsSent(true);
          return cardsNotice(result);
        },
      ),
    [run],
  );

  /**
   * Save a rewritten question. Answers with what went wrong, or `null`, so the
   * editor can stay open on a refusal rather than lose what was typed.
   */
  const saveQuestion = useCallback(
    async (gapId: string, question: string): Promise<string | null> => {
      setPending(gapId);
      setFailure(null);
      setNotice(null);
      try {
        await editQuestion(gapId, question);
        setNotice("해소용 질문을 고쳤습니다.");
        reload();
        return null;
      } catch (error) {
        return error instanceof ApiError && error.status === 422
          ? "개인정보로 보이는 내용이 있거나 길이가 맞지 않아 저장하지 않았습니다. 고쳐서 다시 저장해 주세요."
          : "질문을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";
      } finally {
        setPending(null);
      }
    },
    [reload],
  );

  const choose = useCallback(
    (meetingId: string, templateKey: string) =>
      run(
        "template",
        () => chooseTemplate(meetingId, templateKey),
        "템플릿을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
      ),
    [run],
  );

  return {
    pending,
    failure,
    notice,
    dismiss,
    undoDismiss,
    scheduleNext,
    ask,
    sendToSlack,
    cardsSent,
    saveQuestion,
    choose,
  };
}
