# 0003. Privacy constraints are code-level, not policy

**Status:** Accepted
**Date:** 2026-09-08
**Deciders:** the whole team

## Context

Autune processes meeting recordings: named individuals discussing internal
business, sometimes stating personal data out loud. This is among the most
sensitive data a company produces, and the product is aimed at Korean
organizations subject to PIPA, with public-sector customers later.

Two risks are specific to this product:

**Retained audio.** A recording is a permanent, high-fidelity record of a
conversation people believed was ephemeral. A breach exposing recordings is
categorically worse than one exposing summaries.

**Speech-volume metrics.** Measuring how much each person talks is technically
trivial and turns a productivity tool into a surveillance tool. Once a manager
can see per-person talk time, adoption depends on employees not knowing it
exists.

Writing these as policy — a privacy page, a sentence in the terms — does not
constrain the code. Under deadline pressure, "just keep the audio for debugging"
is a natural shortcut.

## Decision

Privacy constraints are implementation rules with the same standing as the
module boundary, documented in `../architecture/privacy.md` and enforced in code
review:

1. **No raw retention.** The recording is deleted in a `finally` block after
   transcription — on success, on exception, on cancellation. No debug flag
   keeps it.
2. **Mask before store.** PII masking runs before the first write. The unmasked
   string never reaches a database, log, exception message, or external service.
3. **Speaking ratio is private to the speaker.** Delivered by DM, never stored
   in aggregate, never in a contract, no admin override, no small-group
   distributions.
4. **Real deletion.** 90-day default retention, user-initiated deletion, no soft
   deletes. Every module registers a deletion hook.
5. **Consent flow.** Participants are notified; a non-consenting participant's
   speech can be excluded.

Invariant 11 in `/CLAUDE.md` and the review checklist in the privacy document
make these enforceable rather than aspirational.

## Alternatives considered

**Configurable retention, including "keep audio".** Flexible for enterprise
customers who want their own archive. Rejected: it makes the strongest guarantee
optional, and an optional guarantee cannot be stated to users.

**Store audio encrypted, delete after 7 days.** A middle ground that keeps
debugging and retraining possible. Rejected: it preserves the breach risk for a
week and the reason to extend the window later. "The recording is already gone"
is a claim worth more than seven days of debugging convenience.

**Speaking ratios visible to managers, opt-in per team.** A requested feature
that would sell. Rejected: opt-in at the team level is not consent from the
person measured, and it changes what the product is.

**Mask at read time rather than write time.** Keeps the original for correction.
Rejected: it means unmasked text exists in the database, which is the thing the
rule prevents.

## Consequences

**Easier:** a clear answer to a security review, a defensible position under
PIPA, and a real differentiator — no competitor lists automatic PII masking.
Employees have no reason to resist adoption.

**Harder:** debugging a bad transcription is harder without the audio, so
evaluation depends on synthetic fixtures. Model improvement cannot use
production recordings; a training-data collection flow with its own consent is a
separate future decision. Each module carries deletion-hook and privacy-test
work.

**Accepted cost:** we lose the accumulated-audio flywheel that a
retention-friendly competitor would have. The moat is meeting *context* — links,
lineage, patterns — which is derived data and needs no recordings.
