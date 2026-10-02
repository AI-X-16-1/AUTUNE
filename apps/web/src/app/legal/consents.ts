/**
 * What a person has to agree to before the app opens, and which version of
 * each. The consent page lists these; the session gate compares them with what
 * the server recorded for the person (`GET /api/auth/consents`).
 *
 * Kept apart from `./content` on purpose: the gate runs in front of every
 * screen and needs four names and four versions, not the documents' text.
 *
 * **Change a document's wording, change its version here.** A new version is
 * one nobody has agreed to, so everyone is asked again the next time they open
 * the app -- which is the point, and also why a typo fix should not bump it.
 * While the documents are a draft the versions say so; the first published
 * text takes its effective date as its version.
 *
 * All four are required. The last two are separate consents rather than
 * clauses of the policy (decided with the user, 2026-10-02). They are required
 * because the service has no per-person switch that would honour a refusal:
 * voice data and the outside services are settings of a deployment or a team,
 * not of one member. Whether they may be required at all is for the legal
 * review the draft is waiting on.
 */
export type ConsentDocument =
  | "terms"
  | "privacy"
  | "voice_features"
  | "overseas_transfer";

export interface RequiredConsent {
  document: ConsentDocument;
  version: string;
}

export const REQUIRED_CONSENTS: readonly RequiredConsent[] = [
  { document: "terms", version: "draft-1" },
  { document: "privacy", version: "draft-1" },
  { document: "voice_features", version: "draft-1" },
  { document: "overseas_transfer", version: "draft-1" },
];

/** The required consents this person has not given, in the page's order. */
export function missingConsents(
  given: readonly { document: string; version: string }[],
): RequiredConsent[] {
  const have = new Set(given.map((c) => `${c.document}@${c.version}`));
  return REQUIRED_CONSENTS.filter(
    (required) => !have.has(`${required.document}@${required.version}`),
  );
}
