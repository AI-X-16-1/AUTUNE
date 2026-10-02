"use client";

import { useEffect, useState } from "react";

import { listAssignable, type Assignable } from "../api";

/**
 * The members of a meeting's team, for the assignee picker.
 *
 * `null` until the list is known, and again if it could not be read: the
 * picker then falls back to typing a name, which is what it did before there
 * was a list. An empty team list reads the same way to the picker.
 *
 * Carries the meeting it belongs to, as `useActionItems` does for its filter:
 * the detail window stays mounted while the selected card changes, and another
 * meeting's members must not be offered for this one's item.
 */
export function useAssignable(meetingId: string): Assignable[] | null {
  const [state, setState] = useState<{ meetingId: string; members: Assignable[] | null }>({
    meetingId,
    members: null,
  });

  useEffect(() => {
    let current = true;
    listAssignable(meetingId)
      .then((members) => {
        if (current) setState({ meetingId, members });
      })
      .catch(() => {
        if (current) setState({ meetingId, members: null });
      });
    return () => {
      current = false;
    };
  }, [meetingId]);

  return state.meetingId === meetingId ? state.members : null;
}
