"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api/client";

import { assignSpeaker, getSpeakers, listTeamMembers } from "../api";
import type { SpeakerEntry, TeamMember } from "../types";

/**
 * A meeting's speakers, who each one might be, and the people it could be.
 *
 * `StoredTranscript` is the only caller. `LiveTranscript` cannot use this: no
 * `Participant` row exists for a meeting until `persist_transcript` writes
 * them, so `GET /speakers` returns `[]` for the whole time a meeting is
 * `recording` or `analyzing` -- there is nothing yet for a hook like this one
 * to fetch or to let somebody assign. `assign` refetches rather than patching
 * state -- confirming one speaker can change another's candidate, because the
 * profile it just learned is now in the pool.
 *
 * **Three error channels, kept apart on purpose.** `speakersError` and
 * `membersError` are the two `GET`s' own outcome; `assignError` is
 * `assign`'s. A failed `GET /speakers` used to empty `speakers` in its
 * `catch`, which reads exactly like "every speaker is already identified" --
 * a 403, a 500, or a network blip was indistinguishable from nothing left to
 * do. Now a read failure leaves whatever `speakers`/`members` already held
 * (a stale list survives a blip better than an emptied one) and reports
 * itself separately, so a caller can say "화자 목록을 불러오지 못했습니다"
 * rather than silently rendering zero prompts. `membersError` exists for the
 * same reason on the picker: an empty `<select>` with no explanation reads
 * as "nobody on this team", not "the request failed" -- a caller can disable
 * the picker and say why. `assignError` stays its own field rather than
 * folding into either read error, because a click that failed and a fetch
 * that failed are different things a person needs different next steps for;
 * merging them into one message would make one mean both.
 *
 * `pending` is `assign`'s own outcome too -- true for the whole span of that
 * call, *including* the refetch a success triggers, so a caller can disable
 * its controls for exactly as long as the screen might still be showing
 * stale data -- not just for the POST -- closing the double-click hole a
 * second click mid-request would otherwise open.
 *
 * **`speakers`/`speakersError` and `assignError`/`pending` are guarded by a
 * generation counter, not by a per-effect boolean.** `useMeeting` and
 * `useTranscript` each close a fresh `let current = true` over their one
 * effect, which is enough there because only that effect's own fetch can
 * ever write its state, and a superseded generation's binding stays `false`
 * forever once cleanup runs. This hook cannot use the same shape as-is for
 * `speakers`: `assign` is a second, user-triggered path that can write it,
 * and it can outlive a `meetingId` change on its own -- by the time its
 * refetch resolves, the mount effect for the *new* `meetingId` has already
 * run and reset a shared boolean back to `true`, which would make a stale
 * write for the old meeting look current again. `generationRef` is bumped
 * once per `meetingId`; every async write on this path, success or failure,
 * captures its own snapshot of it *before* it awaits anything, and checks
 * that snapshot against `generationRef.current` right before it would call a
 * setter -- so a response (or an error) belonging to a superseded meeting is
 * dropped no matter which write path it came from, or how long it took.
 *
 * `members`/`membersError` keep the plain per-effect `current` binding
 * instead, deliberately not sharing `generationRef`: `members` is a function
 * of `teamId`, not `meetingId` (two meetings can share a team), and gating
 * it on a counter that only tracks `meetingId` would drop a still-valid
 * in-flight fetch for the same team if the meeting changed without `teamId`
 * changing too. It has no second writer the way `speakers` has `assign`, so
 * the simpler binding is also the more correct one here.
 */
export function useSpeakers(meetingId: string, teamId: string | null) {
  const [speakers, setSpeakers] = useState<SpeakerEntry[]>([]);
  const [speakersError, setSpeakersError] = useState<string | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [assignError, setAssignError] = useState<string | null>(null);
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
          if (generationRef.current !== generation) return;
          setSpeakers(entries);
          setSpeakersError(null);
        })
        .catch(() => {
          if (generationRef.current !== generation) return;
          // Leave `speakers` as it is -- a stale list a person can still act
          // on beats one wiped down to "nothing to identify" by a blip.
          setSpeakersError("화자 목록을 불러오지 못했습니다.");
        }),
    [meetingId],
  );

  useEffect(() => {
    void load(generationRef.current);
  }, [load]);

  useEffect(() => {
    if (!teamId) return;
    // No cross-path writer for `members` -- a plain per-effect binding, the
    // same shape `useMeeting`/`useTranscript` use, is enough here, and it is
    // the *more* correct guard for this one: `members` is a function of
    // `teamId`, not `meetingId` (two meetings can share a team), so gating it
    // on `generationRef` -- which only tracks `meetingId` -- would drop a
    // still-valid in-flight fetch for the same team if the meeting changed
    // out from under it without `teamId` changing too. `speakersError`
    // above shares `generationRef` because `speakers` genuinely is a
    // function of `meetingId`; `membersError` shares this effect's own
    // `current` instead, for the same reason `setMembers` already did.
    let current = true;
    listTeamMembers(teamId)
      .then((people) => {
        if (!current) return;
        setMembers(people);
        setMembersError(null);
      })
      .catch(() => {
        if (!current) return;
        // Leave `members` as it is -- same reasoning as `speakers` above.
        setMembersError("참석자 목록을 불러오지 못했습니다.");
      });
    return () => {
      current = false;
    };
  }, [teamId]);

  const assign = useCallback(
    async (speakerLabel: string, userId: string) => {
      const generation = generationRef.current;
      setPending(true);
      setAssignError(null);
      try {
        await assignSpeaker(meetingId, speakerLabel, userId);
        // Awaited, not fired-and-forgotten: `pending` must stay true until
        // this refetch has actually landed, or the confirmed entry's
        // controls re-enable -- and its `<select>` resets to the
        // placeholder -- for the one round-trip before it drops out of the
        // list, which looks like the confirm silently undid itself. `load`
        // reports a failure through `speakersError`, not by rejecting, so it
        // cannot be mistaken here for `assign`'s own failure.
        await load(generation);
      } catch (caught) {
        if (generationRef.current === generation) setAssignError(assignErrorMessage(caught));
      } finally {
        if (generationRef.current === generation) setPending(false);
      }
    },
    [meetingId, load],
  );

  return {
    speakers,
    speakersError,
    members,
    membersError,
    assign,
    assignError,
    pending,
  };
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
