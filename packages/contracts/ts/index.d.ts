/**
 * Generated from packages/contracts (contract version 1.1).
 * Do not edit. Run `pnpm run gen:contracts` and commit the result.
 */

export type ContractVersion = string;
export type MeetingId = string;
export type Id = string;
/**
 * Display label. 'Speaker 2' when unidentified.
 */
export type Speaker = string;
/**
 * None when diarized but not identified. Consumers must handle this.
 */
export type SpeakerId = string | null;
/**
 * None when unidentified or unset.
 */
export type Role = string | null;
/**
 * Seconds from the start of the recording.
 */
export type Start = number;
export type End = number;
/**
 * PII-masked.
 */
export type Text = string;
export type Confidence = number;
export type Utterances = Utterance[];
/**
 * Seconds.
 */
export type Duration = number;
export type Participants = string[];
/**
 * How the audio reached us. Both MVP paths converge on one TranscriptReady.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "TranscriptSource".
 */
export type TranscriptSource = "file_upload" | "web_mic" | "desktop_app";
export type Language = string;
export type OriginalAudioDeleted = boolean;
export type PiiMasked = boolean;
export type ContractVersion1 = string;
export type MeetingId1 = string;
export type Id1 = string;
export type Description = string;
/**
 * None until someone is assigned.
 */
export type AssigneeId = string | null;
export type AssigneeLabel = string | null;
export type DueDate = string | null;
export type SourceUtteranceIds = string[];
/**
 * Action item state. Maps 1:1 to the columns on the action board (S17).
 */
export type ActionStatus = "needs_confirmation" | "todo" | "in_progress" | "done";
export type Confidence1 = number;
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ExternalSystem".
 */
export type ExternalSystem = "notion" | "jira" | "slack";
export type Url = string;
export type ExternalId = string | null;
export type ExternalRefs = ExternalRef[];
export type ActionItems = ActionItem[];
export type UtteranceId = string;
/**
 * The five-way classification module B applies to every utterance.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "UtteranceKind".
 */
export type UtteranceKind = "commitment" | "decision" | "open_question" | "concern" | "ambiguous";
export type Confidence2 = number;
export type NliVerified = boolean;
export type Classifications = Classification[];
export type UtteranceId1 = string;
export type Reason = string;
export type ConfirmationSent = boolean;
export type AmbiguousAgreements = AmbiguousAgreement[];
export type ContractVersion2 = string;
export type MeetingId2 = string;
export type Id2 = string;
export type Category = string;
export type Title = string;
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "GapSeverity".
 */
export type GapSeverity = "high" | "medium" | "low";
export type RiskScore = number;
export type TemplateItem = string | null;
export type RelatedTopicIds = string[];
export type SuggestedQuestion = string | null;
export type Gaps = Gap[];
export type Id3 = string;
export type Label = string;
export type Centrality = number;
export type UtteranceIds = string[];
export type Topics = Topic[];
export type TopicId = string;
export type Spoke = string[];
export type Silent = string[];
export type Participation = Participation1[];
export type ContractVersion3 = string;
export type MeetingId3 = string;
export type TopicLabel = string;
export type LinkedMeetingId = string;
export type LinkedMeetingDate = string;
/**
 * Hybrid retrieval score.
 */
export type Similarity = number;
/**
 * Cross-encoder score; the one to trust.
 */
export type RerankScore = number;
export type TopicLinks = TopicLink[];
export type DecisionId = string;
export type CurrentStatement = string;
export type PreviousStatement = string | null;
export type PreviousMeetingId = string | null;
/**
 * How a decision moved between meetings.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ChangeType".
 */
export type ChangeType = "unchanged" | "modified" | "reversed" | "new";
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "NliLabel".
 */
export type NliLabel = "entailment" | "contradiction" | "neutral";
export type Confidence3 = number;
/**
 * User ids absent when the decision changed. Drives the drift warning.
 */
export type KeyStakeholdersAbsent = string[];
export type DecisionLineage = DecisionChange[];
export type ContractVersion4 = string;
export type MeetingId4 = string;
export type TeamId = string;
export type Grade = "A" | "B" | "C" | "D" | "E" | "F";
export type Value = number;
export type RoleA = string;
export type RoleB = string;
export type Score = number;
export type Alignment = RoleAlignment[];
export type Kind = string;
export type HorizonDays = number;
export type Probability = number;
export type Predictions = Prediction[];
/**
 * Modules that had not reported when this snapshot was built.
 */
export type MissingSources = string[];
/**
 * Action item state. Maps 1:1 to the columns on the action board (S17).
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ActionStatus".
 */
export type ActionStatus1 = "needs_confirmation" | "todo" | "in_progress" | "done";

export interface AutuneContracts {
  TranscriptReady: TranscriptReady;
  ExtractionResult: ExtractionResult;
  GapReport: GapReport;
  ContextLinks: ContextLinks;
  IntelligenceSnapshot: IntelligenceSnapshot;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "TranscriptReady".
 */
export interface TranscriptReady {
  contract_version?: ContractVersion;
  meeting_id: MeetingId;
  utterances: Utterances;
  metadata: TranscriptMetadata;
}
/**
 * One continuous stretch of speech by one speaker.
 *
 * ``text`` is already PII-masked. There is no unmasked form to recover.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "Utterance".
 */
export interface Utterance {
  id: Id;
  speaker: Speaker;
  speaker_id?: SpeakerId;
  role?: Role;
  start: Start;
  end: End;
  text: Text;
  confidence: Confidence;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "TranscriptMetadata".
 */
export interface TranscriptMetadata {
  duration: Duration;
  participants?: Participants;
  source: TranscriptSource;
  language?: Language;
  privacy: PrivacyFlags;
}
/**
 * Proof that module A honored its obligations before publishing.
 *
 * ``original_audio_deleted`` being False means the pipeline is broken.
 * Consumers fail loudly rather than proceeding.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "PrivacyFlags".
 */
export interface PrivacyFlags {
  original_audio_deleted: OriginalAudioDeleted;
  pii_masked: PiiMasked;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ExtractionResult".
 */
export interface ExtractionResult {
  contract_version?: ContractVersion1;
  meeting_id: MeetingId1;
  action_items?: ActionItems;
  classifications?: Classifications;
  ambiguous_agreements?: AmbiguousAgreements;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ActionItem".
 */
export interface ActionItem {
  id: Id1;
  description: Description;
  assignee_id?: AssigneeId;
  assignee_label?: AssigneeLabel;
  due_date?: DueDate;
  source_utterance_ids?: SourceUtteranceIds;
  status?: ActionStatus;
  confidence: Confidence1;
  external_refs?: ExternalRefs;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ExternalRef".
 */
export interface ExternalRef {
  system: ExternalSystem;
  url: Url;
  external_id?: ExternalId;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "Classification".
 */
export interface Classification {
  utterance_id: UtteranceId;
  kind: UtteranceKind;
  confidence: Confidence2;
  nli_verified?: NliVerified;
}
/**
 * Assent too weak to treat as a commitment; the speaker was asked to confirm.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "AmbiguousAgreement".
 */
export interface AmbiguousAgreement {
  utterance_id: UtteranceId1;
  reason: Reason;
  confirmation_sent?: ConfirmationSent;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "GapReport".
 */
export interface GapReport {
  contract_version?: ContractVersion2;
  meeting_id: MeetingId2;
  gaps?: Gaps;
  topics?: Topics;
  participation?: Participation;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "Gap".
 */
export interface Gap {
  id: Id2;
  category: Category;
  title: Title;
  severity: GapSeverity;
  risk_score: RiskScore;
  template_item?: TemplateItem;
  related_topic_ids?: RelatedTopicIds;
  suggested_question?: SuggestedQuestion;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "Topic".
 */
export interface Topic {
  id: Id3;
  label: Label;
  centrality: Centrality;
  utterance_ids?: UtteranceIds;
}
/**
 * Whether a participant spoke on a topic — coverage, not speech volume.
 *
 * This must never become a per-person talk-time metric. See
 * docs/architecture/privacy.md section 3.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "Participation".
 */
export interface Participation1 {
  topic_id: TopicId;
  spoke?: Spoke;
  silent?: Silent;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "ContextLinks".
 */
export interface ContextLinks {
  contract_version?: ContractVersion3;
  meeting_id: MeetingId3;
  topic_links?: TopicLinks;
  decision_lineage?: DecisionLineage;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "TopicLink".
 */
export interface TopicLink {
  topic_label: TopicLabel;
  linked_meeting_id: LinkedMeetingId;
  linked_meeting_date: LinkedMeetingDate;
  similarity: Similarity;
  rerank_score: RerankScore;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "DecisionChange".
 */
export interface DecisionChange {
  decision_id: DecisionId;
  current_statement: CurrentStatement;
  previous_statement?: PreviousStatement;
  previous_meeting_id?: PreviousMeetingId;
  change_type: ChangeType;
  nli_label?: NliLabel | null;
  confidence: Confidence3;
  key_stakeholders_absent?: KeyStakeholdersAbsent;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "IntelligenceSnapshot".
 */
export interface IntelligenceSnapshot {
  contract_version?: ContractVersion4;
  meeting_id: MeetingId4;
  team_id: TeamId;
  quality_score: QualityScore;
  gap_distribution?: GapDistribution;
  alignment?: Alignment;
  predictions?: Predictions;
  missing_sources?: MissingSources;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "QualityScore".
 */
export interface QualityScore {
  grade: Grade;
  value: Value;
}
export interface GapDistribution {
  [k: string]: number;
}
/**
 * Agreement between two roles. Role level, never person level.
 *
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "RoleAlignment".
 */
export interface RoleAlignment {
  role_a: RoleA;
  role_b: RoleB;
  score: Score;
}
/**
 * This interface was referenced by `AutuneContracts`'s JSON-Schema
 * via the `definition` "Prediction".
 */
export interface Prediction {
  kind: Kind;
  horizon_days: HorizonDays;
  probability: Probability;
}
