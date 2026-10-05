/**
 * Calls to /api/audio. This feature calls no other module's endpoints, with one
 * exception: /api/agent/research, where the research card on the meeting screen
 * reads the agent layer's documents for this meeting (Research subagent spec,
 * section 4 ④).
 */
import { api, API_BASE as SAME_ORIGIN_BASE, ApiError, authHeaders } from "@/shared/api/client";

export { api };

import type {
  AccountDeleted,
  MaskingRule,
  MeetingDetail,
  MeetingSummary,
  MyData,
  PiiCategory,
  PiiReported,
  ResearchDocument,
  RetentionDays,
  SpeakerEntry,
  SpeechDeleted,
  TeamMember,
  TeamPrivacy,
  TeamSummary,
  Utterance,
} from "./types";

/** A meeting's stored transcript, masked.
 *
 * `/transcripts/{meeting_id}`, which is what `docs/modules/audio.md` plans and
 * what B (`/results/{meeting_id}`) and D (`/links/{meeting_id}`) already do.
 * This called `/meetings/{id}/transcript`, a path no backend route had — the
 * two halves of one module had picked different shapes for the same call.
 *
 * **Not the live one.** Utterances are written in a single transaction when the
 * task finishes, so this returns nothing until the meeting is processed and
 * cannot show a recording as it happens. `audio.md` gives that to a live
 * channel that goes straight to the screen, "never through a contract or an
 * event" — `liveSocketUrl` below.
 *
 * `Utterance[]`, not `TranscriptReady`: that payload is the announcement A
 * publishes once, and a consumer is required to check
 * `privacy.original_audio_deleted` before touching it. A screen reading a
 * stored transcript is not that consumer. `Utterance` is still a contract type,
 * so no mirror is hand-written here.
 */
export const getTranscript = (meetingId: string) =>
  api.audio<Utterance[]>(`/transcripts/${meetingId}`);

/** A meeting's own row: title, status, and the two privacy flags. What S12 polls. */
export const getMeeting = (meetingId: string) =>
  api.audio<MeetingDetail>(`/meetings/${meetingId}`);

/** S12 "처리 중단". The meeting is `failed` on return and takes a new upload. */
export const cancelTranscription = (meetingId: string) =>
  api.audio<{ meeting_id: string; status: string }>(
    `/meetings/${meetingId}/transcription/cancel`,
    { method: "POST" },
  );

/** S12 "다시 시작": run a stalled meeting again from the upload still on the server. */
export const restartTranscription = (meetingId: string) =>
  api.audio<{ meeting_id: string; status: string }>(
    `/meetings/${meetingId}/transcription/restart`,
    { method: "POST" },
  );

/**
 * Every meeting this person may see, newest first. The home screen's list (S05).
 *
 * Team-scoped by the backend's join through `team_members` — not by a filter
 * here and not by a `team_id` this browser passes, because the caller does not
 * get to say which meetings are theirs. No counts in a row either: an utterance
 * count is one join from a per-person speech volume (privacy.md section 3), and
 * action items and gaps belong to modules B and C, which A may not read. A row
 * links to the screens that own the rest.
 */
export const listMeetings = () => api.audio<MeetingSummary[]>("/meetings");

/** The teams this person may open a meeting for. Feeds `createMeeting`. */
export const listTeams = () => api.audio<TeamSummary[]>("/teams");

/** The token for an invitation link, shown once, and when the link lapses. */
export type InvitationIssued = { token: string; expires_at: string };

/**
 * Invite an address to a team the caller is on (#552). Nobody is added: the
 * answer is a token for a link, the same shape whatever the address.
 */
export const inviteToTeam = (teamId: string, email: string) =>
  api.audio<InvitationIssued>(`/teams/${encodeURIComponent(teamId)}/invitations`, {
    method: "POST",
    body: JSON.stringify({ email }),
  });

/**
 * Join the team an invitation link names, as the signed-in owner of the
 * invited address. Every refusal is the same 404; show one sentence for it.
 */
export const acceptInvitation = (token: string) =>
  api.audio<TeamSummary>("/invitations/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

/** S02: make a workspace with this person on it, and nobody else. */
export const createTeam = (body: { name: string; role?: string }) =>
  api.audio<TeamSummary>("/teams", { method: "POST", body: JSON.stringify(body) });

/**
 * Open a meeting before there is any audio for it (S06, the file-upload path).
 *
 * `started_at` is when the meeting happened, and leaving it out is not free:
 * module B reads `meetings.started_at` to anchor a relative due date, so
 * without it "이번 주 금요일까지" produces an action item with no date at all
 * (#340). Optional here because the backend allows a recording uploaded with
 * no known start; the screen always sends one.
 */
export const createMeeting = (body: {
  title: string;
  team_id: string;
  started_at?: string;
}) =>
  api.audio<{ meeting_id: string; status: string }>("/meetings", {
    method: "POST",
    body: JSON.stringify(body),
  });

/** The meeting's speakers and who each one is or might be (S13, S15). */
export const getSpeakers = (meetingId: string) =>
  api.audio<SpeakerEntry[]>(`/meetings/${meetingId}/speakers`);

/**
 * Confirm who a speaker is. 204, no body; the transcript then carries their id.
 *
 * Not through `api.audio`. `request()` in the shared client treats every
 * `response.ok` as JSON and unconditionally does `await response.json()` —
 * fine for every other call in this feature, all of which return a body, but
 * a 204 has none, and `JSON.parse("")` throws `SyntaxError: Unexpected end of
 * JSON input`. That turned a write that succeeded into a promise that
 * rejects: the server assigns the speaker, the client throws before `load()`
 * ever runs, and the prompt sits there looking like nothing happened. 204 is
 * still the right status for a write with no body to return — bending the
 * route to carry one just to dodge a client bug is the wrong fix.
 * `shared/api/client.ts` is out of bounds for this module, so the workaround
 * lives here: **#359** tracks the shared-client fix, and this is the one
 * other call in the feature that goes to `fetch` directly, for the same
 * reason `uploadRecording` below does.
 *
 * A 403 (the reader, or the named user, is not a member of this meeting's
 * team) and a 404 (unknown speaker label) are real outcomes `useSpeakers`
 * has to show, not just this call's own JSON bug — so a non-2xx response
 * throws the same `ApiError` `request()` throws, carrying the server's own
 * code and message, rather than a bare `Error`. That keeps the error
 * rendering in `useSpeakers`/`UnidentifiedSpeaker` from needing to
 * special-case the one call that cannot go through `request()`.
 */
export async function assignSpeaker(
  meetingId: string,
  speakerLabel: string,
  userId: string,
): Promise<void> {
  // Same origin, like the upload below: a direct call to the API's own port is
  // cross-origin, so the browser sent a CORS preflight the API answers 405 and
  // the assignment never left the page.
  const response = await fetch(
    `${SAME_ORIGIN_BASE}/api/audio/meetings/${meetingId}/speakers/${encodeURIComponent(speakerLabel)}`,
    {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify({ user_id: userId }),
    },
  );
  if (!response.ok) {
    let body: { error?: { code?: string; message?: string; details?: Record<string, unknown> } } | undefined;
    try {
      body = await response.json();
    } catch {
      // Non-JSON error body; fall through to the status text.
    }
    throw new ApiError(
      response.status,
      body?.error?.code ?? "unknown",
      body?.error?.message ?? response.statusText,
      body?.error?.details ?? {},
    );
  }
}

/** The team's people, for the picker. */
export const listTeamMembers = (teamId: string) =>
  api.audio<TeamMember[]>(`/teams/${teamId}/members`);

/**
 * A member's statement that everyone in the recording consented (#190, #283).
 *
 * Sent before the upload, on purpose: module B and C analyse only consented
 * utterances, and a transcript written before this call is one they analyse
 * nothing of. The body can only be `true`; there is no un-attest.
 */
export const attestConsent = (meetingId: string) =>
  api.audio<{ meeting_id: string; attested: boolean }>(
    `/meetings/${meetingId}/consent`,
    {
      method: "POST",
      body: JSON.stringify({ attested: true }),
    },
  );

/**
 * Hand a recording to the pipeline. 202: queued, not done.
 *
 * Not through `api.audio`. The shared client sets `content-type:
 * application/json` on every request and spreads caller headers *after* it,
 * so there is no way to send a multipart body through it without also sending
 * a wrong content type — the browser has to set the boundary itself. This is
 * the one call in the feature that goes to `fetch` directly, with the same
 * base URL and the same error shape; when `@/shared/api/client` can carry a
 * FormData body this goes back through it.
 */
export async function uploadRecording(meetingId: string, file: File) {
  const form = new FormData();
  form.append("file", file, file.name);
  // Same origin as every other call (`""` in the browser, through the /api
  // proxy), so the session cookie rides along. This went straight to
  // localhost:8000, a cross-origin request that carries no cookie: fine on a
  // dev token, a 403 for anyone signed in with Google.
  const response = await fetch(
    `${SAME_ORIGIN_BASE}/api/audio/meetings/${meetingId}/recording`,
    {
      method: "POST",
      headers: authHeaders(),
      body: form,
    },
  );
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      // Non-JSON error body; the status text will have to do.
    }
    throw new Error(message);
  }
  return (await response.json()) as { meeting_id: string; status: string };
}

/**
 * The bearer token this browser holds, or null — for the live socket only.
 * Null in a signed-in tab: the socket's handshake then carries the session
 * cookie, which the live route reads when `hello` has no token (#541).
 *
 * A `WebSocket` cannot carry request headers, so the live channel sends the
 * token in its `hello` frame instead (`useLiveSession`). That needs the raw
 * value, which `authHeaders()` wraps.
 *
 * Read back out of `authHeaders()` rather than from `localStorage` again: the
 * shared client is the one place that decides where a token comes from, and
 * #286 moved it there precisely so a second copy could not drift from it. A
 * second reader of `localStorage["autune.token"]` here would be that copy.
 */
export function getToken(): string | null {
  const header = (authHeaders() as Record<string, string>).authorization;
  return header?.startsWith("Bearer ") ? header.slice("Bearer ".length) : null;
}

/**
 * `ws://` or `wss://` for the live channel, on the page's own origin.
 *
 * Through the same `/api` rewrite as every HTTP call: Next proxies the
 * WebSocket upgrade too (probed against `next dev` — the API logged the
 * handshake as accepted). Same origin keeps the session cookie first-party on
 * the handshake, which is how a Google-signed-in browser authenticates the
 * socket, and leaves no second API address to configure per environment.
 */
export function liveSocketUrl(meetingId: string): string {
  const { protocol, host } = window.location;
  return `${protocol === "https:" ? "wss" : "ws"}://${host}/api/audio/live/${meetingId}`;
}

/** The meeting's research documents the reader may see: approved ones for any member. */
export const getResearch = (teamId: string, meetingId: string) =>
  api.agent<ResearchDocument[]>(
    `/research?team_id=${encodeURIComponent(teamId)}&meeting_id=${encodeURIComponent(meetingId)}`,
  );

/** S29 "내 데이터": counts of what Autune holds about the caller. Only theirs. */
export const getMyData = () => api.audio<MyData>("/me/data");

/**
 * S29 "내 데이터 내려받기 (JSON)". The route answers JSON with an attachment
 * header; `request()` reads it as JSON, so the file is made here from what it
 * returned rather than by navigating to the URL — a navigation would not carry
 * the developer token a session-less local run authenticates with.
 */
export async function downloadMyData(): Promise<void> {
  const body = await api.audio<unknown>("/me/export");
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "autune-my-data.json";
  link.click();
  URL.revokeObjectURL(url);
}

/** S29 "음성 임베딩 삭제". 204 — see `assignSpeaker` for why this goes to `fetch`. */
export async function deleteVoiceProfile(): Promise<void> {
  const response = await fetch(`${SAME_ORIGIN_BASE}/api/audio/me/voice-profile`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new ApiError(response.status, "unknown", response.statusText, {});
  }
}

/** S29 "내 발화 데이터 모두 삭제": my utterances and voice. The account stays. */
export const deleteMySpeech = () =>
  api.audio<SpeechDeleted>("/me/speech", { method: "DELETE" });

/** Account deletion (#358). The server clears the session cookie in the same response. */
export const deleteAccount = () => api.audio<AccountDeleted>("/me", { method: "DELETE" });

/** S29's retention row, for one team. */
export const getTeamPrivacy = (teamId: string) =>
  api.audio<TeamPrivacy>(`/teams/${teamId}/privacy`);

export const setTeamRetention = (teamId: string, retentionDays: RetentionDays) =>
  api.audio<TeamPrivacy>(`/teams/${teamId}/privacy`, {
    method: "PATCH",
    body: JSON.stringify({ retention_days: retentionDays }),
  });

/**
 * S30: mask a span the masker missed. **Offsets, never the text** — the server
 * reads the span from the stored row, so the unmasked string is not in the
 * request, the proxy's log or the API's.
 */
export const reportPiiMiss = (
  meetingId: string,
  utteranceId: string,
  body: {
    start: number;
    end: number;
    category: PiiCategory;
    include_similar: boolean;
    add_rule: boolean;
  },
) =>
  api.audio<PiiReported>(`/meetings/${meetingId}/utterances/${utteranceId}/pii-report`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/** S29's "추가 마스킹 항목": the shapes this team learned from S30 reports. */
export const listMaskingRules = (teamId: string) =>
  api.audio<MaskingRule[]>(`/teams/${teamId}/masking-rules`);

/** Stop masking one shape in later transcripts. Answers the rules that remain. */
export const deleteMaskingRule = (teamId: string, ruleId: number) =>
  api.audio<MaskingRule[]>(`/teams/${teamId}/masking-rules/${ruleId}`, { method: "DELETE" });

