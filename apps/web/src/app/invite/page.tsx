import type { Metadata } from "next";

import { InvitationScreen } from "@/features/transcript";

import { SignInCard } from "../login/SignInCard";

/**
 * Where an invitation link lands (#552): `/invite#<token>`.
 *
 * Outside `(app)` and `(focus)`, so `SessionGate` does not send a signed-out
 * visitor to `/login` and lose the invitation on the way: the screen offers
 * sign-in itself and sign-in comes back here (`redirectTo`).
 *
 * Assembly only: the screen lives in `features/transcript` -- teams and
 * memberships are module A's -- and the sign-in card is the app's, handed in.
 */
export const metadata: Metadata = {
  title: "워크스페이스 초대 · Autune",
  // The link carries a credential in its fragment; nothing here should be indexed.
  robots: { index: false, follow: false },
};

export default function InvitePage() {
  return <InvitationScreen signIn={<SignInCard redirectTo="/invite" />} />;
}
