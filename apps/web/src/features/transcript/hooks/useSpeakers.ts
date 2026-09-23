"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api/client";

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
 * nothing. `pending` is true for the whole span of that call, *including*
 * the refetch a success triggers, so a caller can disable its controls for
 * exactly as long as the screen might still be showing stale data -- not
 * just for the POST -- closing the double-click hole a second click
 * mid-request would otherwise open.
 *
 * **Staleness is guarded by a generation counter, not by a per-effect
 * boolean.** `useMeeting` and `useTranscript` each close a fresh
 * `let current = true` over their one effect, which is enough there because
 * only that effect's own fetch can ever write its state, and a superseded
 * generation's binding stays `false` forever once cleanup runs. This hook
 * cannot use the same shape as-is: `assign` is a second, user-triggered path
 * that can write `speakers`, and it can outlive a `meetingId` change on its
 * own -- by the time its refetch resolves, the mount effect for the *new*
 * `meetingId` has already run and reset a shared boolean back to `true`,
 * which would make a stale write for the old meeting look current again.
 * `generationRef` is bumped once per `meetingId`; every async write below
 * captures its own snapshot of it *before* it awaits anything, and checks
 * that snapshot against `generationRef.current` right before it would call a
 * setter -- so a response belonging to a superseded meeting is dropped no
 * matter which of the two write paths it came from, or how long it took.
 */
export function useSpeakers(meetingId: string, teamId: string | null) {
  const [speakers, setSpeakers] = useState<SpeakerEntry[]>([]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const generationRef = useRef(0);
  useEffect(() => {
    generationRef.current += 1;
  }, [meetingId]);

  // Takes the generation to guard as a parameter rather than reading
  // `generationRef.current` itself at call time: the mount effect below
  // calls it right after the bump above, so reading it there is the current
  // generation either way, but `assign` calls it *after* an `await`, when a
  // newer `meetingId` may already have bumped the ref again. `assign` must
  // pass the snapshot it took when *it* started, or its own refetch would
  // stop guarding against exactly the case it exists to guard against.
  const load = useCallback(
    (generation: number) =>
      getSpeakers(meetingId)
        .then((entries) => {
          if (generationRef.current === generation) setSpeakers(entries);
        })
        .catch(() => {
          if (generationRef.current === generation) setSpeakers([]);
        }),
    [meetingId],
  );

  useEffect(() => {
    void load(generationRef.current);
  }, [load]);

  useEffect(() => {
    if (!teamId) return;
    // No cross-path writer for `members` -- a plain per-effect binding, the
    // same shape `useMeeting`/`useTranscript` use, is enough here.
    let current = true;
    listTeamMembers(teamId)
      .then((people) => {
        if (current) setMembers(people);
      })
      .catch(() => {
        if (current) setMembers([]);
      });
    return () => {
      current = false;
    };
  }, [teamId]);

  const assign = useCallback(
    async (speakerLabel: string, userId: string) => {
      const generation = generationRef.current;
      setPending(true);
      setError(null);
      try {
        await assignSpeaker(meetingId, speakerLabel, userId);
        // Awaited, not fired-and-forgotten: `pending` must stay true until
        // this refetch has actually landed, or the confirmed entry's
        // controls re-enable -- and its `<select>` resets to the
        // placeholder -- for the one round-trip before it drops out of the
        // list, which looks like the confirm silently undid itself.
        await load(generation);
      } catch (caught) {
        if (generationRef.current === generation) setError(assignErrorMessage(caught));
      } finally {
        if (generationRef.current === generation) setPending(false);
      }
    },
    [meetingId, load],
  );

  return { speakers, members, assign, error, pending };
}

/**
 * Korean, by the status this one endpoint actually returns -- never the
 * server's own message, which `AutuneError` writes in English (repo policy:
 * error strings are English) and which is exactly right for a log line but
 * wrong for a person reading a `role="alert"` next to a button they just
 * clicked. Switches on `ApiError.status`, not on the message text, so a
 * wording change on the backend cannot silently fall through to the
 * fallback sentence.
 */
function assignErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 403) {
      return "이 화자를 지정할 권한이 없습니다. 본인과 지정하려는 사람 모두 이 회의의 팀에 속해 있는지 확인해 주세요.";
    }
    if (caught.status === 404) {
      return "이 화자를 찾을 수 없습니다. 새로고침한 뒤 다시 시도해 주세요.";
    }
  }
  return "화자를 지정하지 못했습니다. 잠시 후 다시 시도해 주세요.";
}
