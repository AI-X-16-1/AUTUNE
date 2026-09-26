/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type {
  TranscriptReady,
  Utterance,
  UtteranceKind,
} from "@autune/contracts";

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

/**
 * What module B calls each kind, in the order the rail lists them.
 *
 * One map, because a row's tag and the rail's tally name the same thing. They
 * were two maps and a renamed label would have made the row and the rail
 * disagree about what B found.
 */
export const KIND_LABELS: Record<
  import("@autune/contracts").UtteranceKind,
  string
> = {
  commitment: "약속",
  decision: "결정",
  open_question: "질문",
  concern: "우려",
  ambiguous: "확인 필요",
};

/**
 * Module A's own API bodies — `/api/audio/meetings/{id}` and `/api/audio/teams`.
 *
 * Hand-written, and that is not a violation of the rule above: a contract is
 * what another *module* consumes, and nothing here crosses that line. These
 * mirror `modules/audio/src/autune_audio/schemas.py`, which is the one place
 * they may change; `pnpm run gen:contracts` does not generate them.
 */
export type MeetingStatus =
  | "scheduled"
  | "recording"
  | "analyzing"
  | "awaiting_confirmation"
  | "complete"
  | "delivered"
  | "failed";

export type MeetingDetail = {
  meeting_id: string;
  title: string;
  status: MeetingStatus;
  /** Set by the pipeline once the recording is confirmed gone from disk. */
  original_audio_deleted: boolean;
  /** Set in the same transaction as the utterances; false until then. */
  pii_masked: boolean;
  /** The meeting's own team. Feeds `listTeamMembers` for the speaker picker. */
  team_id: string;
};

export type TeamSummary = { team_id: string; name: string };

/**
 * Module A's own speaker endpoints — `/api/audio/meetings/{id}/speakers` and
 * `/api/audio/teams/{id}/members`.
 *
 * Hand-written for the same reason as `MeetingDetail` above: a contract is
 * what another *module* consumes, and these are module A's own response
 * shapes, not shared across the boundary. They mirror
 * `modules/audio/src/autune_audio/schemas.py`.
 */
export type SpeakerCandidate = {
  user_id: string;
  name: string;
  similarity: number;
};

export type SpeakerEntry = {
  speaker_label: string;
  user_id: string | null;
  candidate: SpeakerCandidate | null;
};

export type TeamMember = { user_id: string; name: string };
