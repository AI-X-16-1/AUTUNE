"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { assignSpeaker, getSpeakers, listTeamMembers } from "../api";
import type { SpeakerEntry, TeamMember } from "../types";

/**
 * A meeting's speakers, who each one might be, and the people it could be.
 *
 * Both transcripts use it: the stored one to offer a candidate, the live one
 * to let somebody assign a speaker before the recording has been processed.
 * `assign` refetches rather than patching state -- confirming one speaker can
 * change another's candidate, because the profile it just learned is now in
 * the pool.
 *
 * `error` and `pending` are `assign`'s own outcome, not the two `GET`s'. A
 * 403 (the reader, or the named user, is not a member of the team) and a 404
 * (unknown label) are real answers `POST .../speakers/{label}` gives, and a
 * caller rendering nothing for them is a click that looks like it did
 * nothing. `pending` is true for exactly the span of that call, so a caller
 * can disable its controls and close the double-click hole a second click
 * mid-request would otherwise open.
 */
export function useSpeakers(meetingId: string, teamId: string | null) {
  const [speakers, setSpeakers] = useState<SpeakerEntry[]>([]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Guards every `setState` below against a response landing after
  // `meetingId` has moved on -- the shape `useMeeting`'s and `useTranscript`'s
  // local `current` use, as a ref so `assign`'s own refetch can share it with
  // the mount effect instead of each keeping a separate flag.
  const currentRef = useRef(true);
  useEffect(() => {
    currentRef.current = true;
    return () => {
      currentRef.current = false;
    };
  }, [meetingId]);

  const load = useCallback(() => {
    getSpeakers(meetingId)
      .then((entries) => {
        if (currentRef.current) setSpeakers(entries);
      })
      .catch(() => {
        if (currentRef.current) setSpeakers([]);
      });
  }, [meetingId]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!teamId) return;
    listTeamMembers(teamId)
      .then((people) => {
        if (currentRef.current) setMembers(people);
      })
      .catch(() => {
        if (currentRef.current) setMembers([]);
      });
  }, [teamId]);

  const assign = useCallback(
    async (speakerLabel: string, userId: string) => {
      setPending(true);
      setError(null);
      try {
        await assignSpeaker(meetingId, speakerLabel, userId);
        load();
      } catch (caught) {
        if (currentRef.current) {
          setError(caught instanceof Error ? caught.message : "화자를 지정하지 못했습니다.");
        }
      } finally {
        if (currentRef.current) setPending(false);
      }
    },
    [meetingId, load],
  );

  return { speakers, members, assign, error, pending };
}
