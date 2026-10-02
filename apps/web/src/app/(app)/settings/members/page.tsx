import { MembersSettingsScreen } from "@/features/transcript";

/**
 * 설정 › 구성원 (#552): the workspace's members, and inviting somebody to it.
 *
 * Assembly only: the screen lives in `features/transcript`, because teams and
 * memberships are module A's to write.
 */
export default function MembersSettingsPage() {
  return <MembersSettingsScreen />;
}
