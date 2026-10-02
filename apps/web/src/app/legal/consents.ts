/**
 * What a person has to agree to before the app opens, and which version of
 * each. The consent page lists these; the session gate compares them with what
 * the server recorded for the person (`GET /api/auth/consents`).
 *
 * Kept apart from `./content` on purpose: the gate runs in front of every
 * screen and needs two names and two versions, not the documents' text.
 *
 * **Change a document's wording, change its version here.** A new version is
 * one nobody has agreed to, so everyone is asked again the next time they open
 * the app -- which is the point, and also why a typo fix should not bump it.
 * While the documents are a draft the versions say so; the first published
 * text takes its effective date as its version.
 *
 * **Two documents, and it stays two until something else changes first.** The
 * page briefly listed two more -- a consent to voice feature data and one to
 * transfer abroad -- and they were taken out again the same day (the user's
 * choice after the privacy owner's review of #715). Those are consents a
 * person must be able to refuse and to withdraw, and here they could do
 * neither: the service has no per-person switch that would honour a refusal,
 * and the record can only say "agreed". The server refuses any other name
 * (`consents.DOCUMENTS`), so adding a row here is not enough to bring one
 * back -- it needs a record of withdrawal and #92's answer.
 */
export type ConsentDocument = "terms" | "privacy";

export interface RequiredConsent {
  document: ConsentDocument;
  version: string;
}

export const REQUIRED_CONSENTS: readonly RequiredConsent[] = [
  { document: "terms", version: "draft-1" },
  { document: "privacy", version: "draft-1" },
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
