"use client";

import { useEffect, useState } from "react";

import { getCloudModelUse } from "../api";

/**
 * #392's operating rule, said where a recording is put in (#392, 2026-10-09):
 * on a server that sends meeting text to a cloud model, only the team's own
 * meetings go in, and none with a participant from outside the team, because
 * a name that is not on the team's roster is not replaced and leaves as it
 * was spoken.
 *
 * **A notice, not a check.** Nothing here can know who was in the room; it
 * gates no button and the consent step beside it is unchanged.
 *
 * **Drawn only where the rule applies.** `GET /cloud-model` says whether this
 * server sends meeting text out, and until it has said yes nothing is drawn --
 * not while it loads and not when it fails, since the sentence would be a
 * restriction that is not true of a server that sends nothing.
 *
 * The two screens that take a recording belong to module A, and a feature does
 * not import another feature: their pages pass this in as a slot, and say
 * which of the two it is.
 */

/**
 * Both halves of the rule, for each way a recording comes in: the upload form
 * speaks of 올리기, and the live gate's heading and button both say 녹음 시작.
 * The second sentence is the half the rule exists for -- a meeting the team
 * held with a customer or an applicant reads as "our own meeting" too. The
 * wording is module B's owner's; the second sentences are the ones
 * @mminjae97 proposed in the review of #1134.
 */
export const OWN_TEAM_MEETINGS_ONLY = {
  upload:
    "우리 팀 자신의 회의만 올려 주세요. 팀 밖 사람이 참석한 회의는 올리지 마세요.",
  live: "우리 팀 자신의 회의만 녹음해 주세요. 팀 밖 사람이 참석하면 녹음하지 마세요.",
} as const;

export function OwnTeamMeetingsNotice({
  entrance,
  className,
}: {
  entrance: keyof typeof OWN_TEAM_MEETINGS_ONLY;
  className?: string;
}) {
  const [inUse, setInUse] = useState(false);

  useEffect(() => {
    let current = true;
    getCloudModelUse()
      .then((use) => {
        if (current) setInUse(use.in_use);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, []);

  if (!inUse) return null;
  return (
    <p
      role="note"
      className={className}
      style={{
        fontSize: "var(--text-meta)",
        fontWeight: 600,
        color: "var(--color-ink-strong)",
      }}
    >
      {OWN_TEAM_MEETINGS_ONLY[entrance]}
    </p>
  );
}
