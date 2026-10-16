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
 * text takes its effective date as its version. A text whose wording changes
 * again with the same effective date keeps that date and counts after a dot
 * -- `2026-10-12.2` is the second text effective 2026-10-12 -- so the version
 * still names the day the documents carry, and reads and sorts as the later
 * one.
 *
 * `2026-10-12.2` (the owner, 2026-10-12): the sentences about a team's
 * uploaded documents (#817) came into both documents on the effective day
 * itself, as part of the first text and with no amendment notice. But
 * `2026-10-12` had been on `main` with this page for some hours by then, and
 * a person who agreed to it agreed to a text without those sentences. The
 * version moves so that they are asked again, whichever was deployed first.
 *
 * `2026-10-12.3`, the privacy policy alone: the lines about S20's messages on
 * a team's Slack channel and the gaps "다음 회의 잡기" writes into a person's
 * own calendar event (#824) came in after `2026-10-12.2`. The terms did not
 * change, so theirs stays.
 *
 * `2026-10-12.4`, the privacy policy alone: two of its sentences about the
 * Meeting Context Engine no longer matched the code. The channel's notice
 * of a changed decision was said to show how many did not speak; it has
 * shown no number since #339. And a person deleting their own speech was
 * told the decision statements kept for meeting linking would still hold
 * it; they have been cleared on that deletion since #587. The terms did not
 * change, so theirs stays.
 *
 * `2026-10-12.5` for the privacy policy and `2026-10-12.3` for the terms (the owner,
 * 2026-10-12), one rise for two things. A refused upload is told to the
 * team's approvers by Slack (#1201): the policy says what that message
 * carries, that the record keeps the time it went, and when the record is
 * deleted. And a team's uploaded documents are searched (#817): both
 * documents name the search, and the policy says what happens to the
 * question a person types. The search's sentences came in here and not
 * with the search, which answers 404 while uploads are off.
 *
 * `2026-10-12.6` for the privacy policy and `2026-10-12.4` for the terms (the
 * owner, 2026-10-13). A team with no member left loses its uploaded documents,
 * its links and its records of refused files at an hourly check, whatever it
 * chose about keeping them (#817). The policy had said a person's withdrawal
 * does not delete a team's documents; for a team's only member that is no
 * longer the whole of it, so a person who agreed to `.5` agreed to a text that
 * told them otherwise. Both documents also said "until the team is deleted"
 * where they now add "or has no member left". The effective date stays.
 * The same rise carries four corrections of what the documents said of a
 * team's uploaded documents (the owner, 2026-10-13): the policy lists the
 * vectors computed from the masked text and the model's name among what is
 * kept; it says a number or an address split by a line or page break can be
 * kept unmasked, that a material has no way to mask one part and can be
 * deleted, and that the check before storing is the same method again; the
 * terms say reporting a missed part is for the transcript; and the policy
 * says a server's access record can hold the requested address.
 *
 * `2026-10-12.7` for the privacy policy alone: the service moves to AWS's
 * Seoul region (#1342). The policy said the keeping of what the service stores
 * was not entrusted and that a Cloudflare tunnel carried the connection to a
 * server the team runs; on the release host both are untrue, and a person
 * who agreed to `.6` agreed to a text that said so. Article 6 lists AWS,
 * the Cloudflare rows of articles 3, 6 and 7 stay but are scoped to the dev
 * site, which is still reached through the tunnel, and article 5 (3) says
 * where audio is processed while it is. The terms did not change, so theirs
 * stays. **The policy's effective date moves to 2026-10-15** (the day the
 * release host opens), on its own constant so the terms keep theirs. Article
 * 15 (2) asks for 7 days' notice, which this does not give; the release host
 * has no earlier user, and the policy owners' agreement is on the pull request.
 *
 * `2026-10-12.8`, the privacy policy alone: the landing page (autune.cloud)
 * carries the Meta Pixel (the privacy owner, 2026-10-15). The policy had said
 * no behavioural data is collected for advertising and no outside tool
 * analyses use (제12조 ⑥), and that nothing is given to a third party for
 * advertising (제5조 ④); a person who agreed to `.7` agreed to that. 제12조
 * now names Meta as the collector, what it collects, that the service itself
 * carries no pixel, and the refusal button on the landing page. The effective
 * date stays 2026-10-15, the same day as `.7`, so the version counts after
 * the dot. The terms did not change, so theirs stays.
 *
 * `2026-10-12.9`, the privacy policy alone (the privacy owner, 2026-10-16):
 * the pixel's `_fbp` cookie is set on `.autune.cloud`, so the browser also
 * sends it with every request to the service at app.autune.cloud. 제12조 ⑦
 * now says so, and that the service neither reads nor keeps it nor passes it
 * on. Nothing new is collected, but a person who agreed to `.8` read a text
 * that implied the cookie stayed on the landing page. The effective date
 * stays 2026-10-15. The terms did not change, so theirs stays.
 *
 * `2026-10-12.10`, the privacy policy alone (#1369): a member may choose a
 * file from their own Google Drive for the 자료 upload, in their own browser,
 * and the 자료 screen then loads Google's scripts. 제2조 and 제2조 ⑤ say how the
 * file arrives and that the server never reaches Drive or keeps a Drive token
 * or file id; 제12조 ⑨ says Google's scripts load only when the button is
 * pressed and that Google may collect browser information under its own
 * policy. A person who agreed to `.9` read a text with no Drive path and no
 * Google script on the service. The same version also corrects the 자료 row of
 * 제2조: the upload form now fills an empty title with the chosen file's name
 * (extension dropped), so "the file's name is not stored" is true only when
 * the member changes the title (mkkim68's review of #1370). The effective
 * date stays 2026-10-15; whether
 * 제15조 (2)'s 7 days apply to this is the owners' call, on the pull request.
 * The terms did not change, so theirs stays.
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
  { document: "terms", version: "2026-10-12.4" },
  { document: "privacy", version: "2026-10-12.10" },
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
