"use client";

import { useEffect, useState } from "react";

import { listMeetings } from "../api";
import type { MeetingSummary } from "../types";

export type MeetingsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; meetings: MeetingSummary[] };

/**
 * The meetings this person may see -- one team's when `teamId` is given, as
 * the home screen gives it. The home screen's data.
 *
 * Three states and no fourth, the same shape as `useTranscript`. **An empty
 * list is `ready` with no rows, not an error and not a spinner that never
 * stops** — a new install has no meetings, and that is the first state a
 * reviewer sees. The screen says "아직 회의가 없습니다"; a hook that turned it
 * into an error would make a working product look broken.
 *
 * It does not poll. `useMeeting` polls one meeting because the pipeline moves
 * it through states while somebody watches; a list of meetings changes when
 * somebody uploads one, which is a navigation, not a tick. A three-second loop
 * over every meeting the person can see would be a live feed that is wrong
 * about how live it is — the same argument `useTranscript` makes. Uploading a
 * meeting navigates away and back, and the fetch on mount is what shows it.
 *
 * The request is abandoned if the component goes away before it lands, so a
 * fast navigation cannot set state on something that is no longer mounted.
 */
export function useMeetings(teamId?: string): MeetingsState {
  // What was answered, kept with the team it was answered FOR. A bare state
  // would still hold the last team's list on the render that first sees the
  // next team -- the effect that clears it has not run yet -- and that list
  // would be drawn once under the new team's name (review of #839).
  const [answer, setAnswer] = useState<Answer>({ teamId, state: LOADING });

  useEffect(() => {
    let current = true;

    listMeetings(teamId)
      .then((meetings) => {
        if (current) setAnswer({ teamId, state: { status: "ready", meetings } });
      })
      .catch((error: unknown) => {
        // The message, never the body: an API error can quote what it refused.
        const message =
          error instanceof Error
            ? error.message
            : "회의 목록을 불러오지 못했습니다";
        if (current) setAnswer({ teamId, state: { status: "error", message } });
      });

    return () => {
      current = false;
    };
    // Another team is another list: asked for again, and an answer for the
    // team just left is dropped by `current`.
  }, [teamId]);

  // An answer for another team is not this team's: until this one's lands,
  // the list is loading.
  return answer.teamId === teamId ? answer.state : LOADING;
}

type Answer = { teamId: string | undefined; state: MeetingsState };

const LOADING: MeetingsState = { status: "loading" };
