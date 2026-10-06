"use client";

import { useCallback, useState } from "react";

import { askGap, carryMeeting, chooseTemplate, dismissGap, undoDismissGap } from "../api";
import type { AgendaOutcome, AskOutcome, GapAsk, GapMeetingCarry } from "../types";

/** The second half of what "다음 회의 잡기" says, by what the calendar did. */
export const AGENDA_NOTICE: Record<AgendaOutcome, string | null> = {
  added: "다음 회의 일정 설명에 추가했습니다.",
  removed: "다음 회의 일정 설명에서 뺐습니다.",
  no_next_meeting: "예정된 다음 회의가 없어 캘린더에는 넣지 못했습니다.",
  no_event: "내 Google 캘린더에서 다음 회의 일정을 찾지 못했습니다.",
  not_connected: "Google 캘린더가 연결되어 있지 않아 캘린더에는 넣지 못했습니다.",
  reconnect_required: "Google 캘린더를 다시 연결해야 합니다.",
  failed: "캘린더에 쓰지 못했습니다.",
  not_tried: null,
};

/** What the screen says after "다음 회의 잡기". */
export function meetingCarryNotice(result: GapMeetingCarry): string {
  if (result.carried === 0) return "다음 회의로 넘길 갭이 없습니다.";
  const calendar = AGENDA_NOTICE[result.calendar];
  return `갭 ${result.carried}건을 다음 회의로 넘겼습니다.${calendar ? ` ${calendar}` : ""}`;
}

/** What the screen says after "담당자 지정해 질문". */
export const ASK_NOTICE: Record<AskOutcome, string> = {
  added: "질문을 담당자의 Google 캘린더에 넣었습니다.",
  already_asked: "이미 이 담당자에게 보낸 질문입니다.",
  not_connected: "이 담당자는 Google 캘린더를 연결하지 않아 질문을 넣지 못했습니다.",
  reconnect_required: "이 담당자의 Google 캘린더 연결이 끊겨 질문을 넣지 못했습니다.",
  failed: "캘린더에 질문을 넣지 못했습니다. 잠시 후 다시 시도해 주세요.",
};

/**
 * The writes S20 makes: dismissing a gap and taking that back, sending the
 * meeting's open gaps on to the next meeting and asking a teammate a gap's
 * question (#824), and holding the meeting to another template.
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

  const choose = useCallback(
    (meetingId: string, templateKey: string) =>
      run(
        "template",
        () => chooseTemplate(meetingId, templateKey),
        "템플릿을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
      ),
    [run],
  );

  const ask = useCallback(
    (gapId: string, userId: string) =>
      run(
        gapId,
        () => askGap(gapId, userId),
        "질문을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
        (result: GapAsk) => ASK_NOTICE[result.outcome],
      ),
    [run],
  );

  return { pending, failure, notice, dismiss, undoDismiss, scheduleNext, ask, choose };
}
