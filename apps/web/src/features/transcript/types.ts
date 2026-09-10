/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type { TranscriptReady, Utterance, UtteranceKind } from "@autune/contracts";

/**
 * What S13 renders per row: an utterance, plus the classification module B may
 * or may not have produced for it yet.
 *
 * The kind is separate rather than folded into `Utterance` because it arrives
 * later and from another module. A live transcript shows rows the moment they
 * are transcribed; the tag appears when B has said something about them, and
 * most rows never get one — a meeting is mostly not commitments and decisions.
 */
export type LiveRow = {
  utterance: import("@autune/contracts").Utterance;
  kind?: import("@autune/contracts").UtteranceKind;
};

/** Recording state, which drives the frame glow and the right-rail controls. */
export type RecordingState = "recording" | "paused" | "ended";
