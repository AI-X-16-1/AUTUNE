"use client";

import { ApproverSettingsScreen } from "@/features/agent";
import { TeamScope } from "@/features/transcript";

/**
 * 설정 › 승인자 (#592).
 *
 * Assembly only: the screen is the agent layer's and takes a team id;
 * `TeamScope` is module A's and knows which teams this person belongs to. A
 * client component because the hand-off is a render function.
 */
export default function ApproverSettingsPage() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <div className="max-w-[760px]">
        <TeamScope>
          {(teamId) => <ApproverSettingsScreen teamId={teamId} />}
        </TeamScope>
      </div>
    </main>
  );
}
