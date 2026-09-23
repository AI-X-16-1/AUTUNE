"use client";

import { useCallback, useEffect, useState } from "react";

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
 */
export function useSpeakers(meetingId: string, teamId: string | null) {
  const [speakers, setSpeakers] = useState<SpeakerEntry[]>([]);
  const [members, setMembers] = useState<TeamMember[]>([]);

  const load = useCallback(() => {
    getSpeakers(meetingId)
      .then(setSpeakers)
      .catch(() => setSpeakers([]));
  }, [meetingId]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!teamId) return;
    listTeamMembers(teamId)
      .then(setMembers)
      .catch(() => setMembers([]));
  }, [teamId]);

  const assign = useCallback(
    async (speakerLabel: string, userId: string) => {
      await assignSpeaker(meetingId, speakerLabel, userId);
      load();
    },
    [meetingId, load],
  );

  return { speakers, members, assign };
}
