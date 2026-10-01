import { PrivacySettingsScreen } from "@/features/transcript";

/**
 * S29, 설정 › 개인정보 · 보관 (#554).
 *
 * Assembly only: the screen lives in `features/transcript`, because deletion,
 * voice embeddings, consent and masking are all module A's data.
 */
export default function PrivacySettingsPage() {
  return <PrivacySettingsScreen />;
}
