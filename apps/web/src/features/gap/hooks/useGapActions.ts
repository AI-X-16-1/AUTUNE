"use client";

import { useCallback, useState } from "react";

import { carryGap, chooseTemplate, dismissGap, undoCarryGap, undoDismissGap } from "../api";

/**
 * The writes S20 makes: dismissing a gap and sending one on to the next meeting
 * (#824), taking either back, and holding the meeting to another template.
 *
 * **Every write is followed by a read, never by a local edit.** The server
 * decides what a dismissal does to the report and the rail — the gap leaves
 * one and is marked on the other — and choosing a template re-runs the whole
 * comparison. Patching the screen's copy instead would be this feature
 * re-implementing those rules in a second place, which is how the two come to
 * disagree.
 *
 * `pending` names what is in flight (a gap id, or `"template"`) so exactly that
 * control can show it.
 */
export function useGapActions(reload: () => void) {
  const [pending, setPending] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const run = useCallback(
    async (key: string, write: () => Promise<unknown>, message: string) => {
      setPending(key);
      setFailure(null);
      try {
        await write();
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

  const carry = useCallback(
    (gapId: string) =>
      run(
        gapId,
        () => carryGap(gapId),
        "갭을 다음 회의로 넘기지 못했습니다. 잠시 후 다시 시도해 주세요.",
      ),
    [run],
  );

  const undoCarry = useCallback(
    (gapId: string) =>
      run(
        gapId,
        () => undoCarryGap(gapId),
        "다음 회의로 넘긴 것을 되돌리지 못했습니다. 잠시 후 다시 시도해 주세요.",
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

  return { pending, failure, dismiss, undoDismiss, carry, undoCarry, choose };
}
