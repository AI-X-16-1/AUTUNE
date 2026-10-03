"use client";

import { useEffect, useState } from "react";

import { listTeamMembers } from "../api";
import type { TeamMember } from "../types";
import { TeamInvite } from "./TeamInvite";
import { TeamScope } from "./TeamScope";

/**
 * 설정 › 구성원 (#552): who is on the workspace, and inviting somebody to it.
 *
 * S02 offers the invitation right after a workspace is made; this is the same
 * control for a workspace that already exists -- the one a team has connected
 * its tools to and holds its meetings in. Without it, inviting anybody meant
 * making a new, empty workspace.
 *
 * The list is the team's members by name, as the speaker picker already shows
 * them. Pending invitations are not listed: nothing reads them back, on
 * purpose (`invitations.py`).
 */

const SECTION_TITLE = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-body)",
} as const;
const META = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;

export function MembersSettingsScreen() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page) var(--space-page)" }}>
      <div className="max-w-[760px]">
        <TeamScope>{(teamId) => <Members key={teamId} teamId={teamId} />}</TeamScope>
      </div>
    </main>
  );
}

function Members({ teamId }: { teamId: string }) {
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    listTeamMembers(teamId)
      .then((list) => {
        if (current) setMembers(list);
      })
      .catch(() => {
        if (current) setFailed(true);
      });
    return () => {
      current = false;
    };
  }, [teamId]);

  return (
    <>
      <section aria-labelledby="members-title">
        <h2 id="members-title" style={SECTION_TITLE}>
          구성원
        </h2>
        {failed ? (
          <p className="mt-2" style={META}>
            구성원 목록을 불러오지 못했습니다.
          </p>
        ) : members === null ? (
          <p className="mt-2" style={META}>
            구성원을 불러오는 중입니다.
          </p>
        ) : (
          <ul className="mt-2 border-t border-[var(--color-hairline)]">
            {members.map((member) => (
              <li
                key={member.user_id}
                className="border-b border-[var(--color-hairline)] text-[var(--color-ink-strong)]"
                style={{ paddingBlock: "var(--space-8)", fontSize: "var(--text-rowBody)" }}
              >
                {member.name}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-8" aria-labelledby="invite-title">
        <h2 id="invite-title" className="mb-2" style={SECTION_TITLE}>
          팀원 초대
        </h2>
        <TeamInvite teamId={teamId} />
      </section>
    </>
  );
}
