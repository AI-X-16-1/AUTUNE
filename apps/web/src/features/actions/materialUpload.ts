import { ApiError } from "@/shared/api/client";

import { localToday } from "./dates";
import { typedTextRefusal } from "./refusal";
import type { MaterialNotRead, MaterialUploadRules } from "./types";

/**
 * What the 자료 screen says about a file a member sends (#817): whether to
 * send it at all, what a refusal means in the member's words, and what a
 * stored row promises.
 *
 * Rules and sentences only, no component: the screen has one place to change
 * a sentence, and each can be tested without a form around it.
 *
 * **The server decides; this only says so sooner or in Korean.** A file is
 * refused here before it is sent only by the two things the server's own
 * rules give as numbers -- the name's ending and the size
 * (`MaterialUploadRules`). Everything else needs the file read, which only
 * the server does.
 *
 * **Nothing here is told what a file says.** A refusal carries a reason code
 * and never a word of the file, and a sentence built from it names the rule,
 * not the content.
 */

/** The ending the server reads a file's type from: ".pdf", in lower case; "" for none. */
export function suffixOf(name: string): string {
  const dot = name.lastIndexOf(".");
  // A name that is only an ending (".pdf") has none, as the server reads it.
  return dot <= 0 ? "" : name.slice(dot).toLowerCase();
}

/** A size in the unit the limit is given in: "10MB", "1.5MB". */
export function megabytes(bytes: number): string {
  const value = bytes / (1024 * 1024);
  return `${Number.isInteger(value) ? value : value.toFixed(1)}MB`;
}

const tooLarge = (rules: MaterialUploadRules | null) =>
  rules
    ? `파일이 너무 큽니다. 한 파일은 ${megabytes(rules.max_bytes)}까지 올릴 수 있습니다.`
    : "파일이 너무 큽니다.";

const wrongType = (rules: MaterialUploadRules | null) =>
  rules
    ? `이 형식의 파일은 받지 않습니다. 받는 형식: ${rules.suffixes.join(" ")}`
    : "이 형식의 파일은 받지 않습니다.";

/** The server's reasons a file cannot be read, as it names them. */
const READ = {
  protected: "암호가 걸린 파일은 읽을 수 없습니다. 암호를 풀고 다시 올려 주세요.",
  no_text_layer:
    "글자 정보가 없는 PDF입니다. 스캔한 문서처럼 그림으로만 된 글은 읽지 않습니다.",
  page_without_text:
    "그림으로만 된 쪽이 있는 PDF는 받지 않습니다. 그 쪽에 무엇이 적혀 있는지 확인할 수 없기 때문입니다.",
  damaged: "파일이 손상되어 읽을 수 없습니다.",
  encoding: "글자를 읽을 수 없는 파일입니다. UTF-8로 저장해 다시 올려 주세요.",
  empty: "읽을 글이 없는 파일입니다.",
  too_long: "글이 너무 깁니다. 한 파일의 글은 30만 자까지 받습니다.",
} as const satisfies Record<string, string>;

/**
 * Why this file is not worth sending, or null when the server should look.
 * The same two checks the server makes first, so the answer is the same and
 * comes without the upload.
 */
export function fileRefusal(
  file: { name: string; size: number },
  rules: MaterialUploadRules,
): string | null {
  const accepted = rules.suffixes.map((suffix) => suffix.toLowerCase());
  if (!accepted.includes(suffixOf(file.name))) return wrongType(rules);
  if (file.size === 0) return READ.empty;
  if (file.size > rules.max_bytes) return tooLarge(rules);
  return null;
}

const READ_MESSAGE = /the file cannot be read: ([a-z_]+)/;

/**
 * The reason code of a file the server could not read. From the details when
 * they carry one, else from the message, which ends with it.
 */
function readReason(cause: ApiError): string | null {
  if (typeof cause.details.reason === "string") return cause.details.reason;
  return READ_MESSAGE.exec(cause.message)?.[1] ?? null;
}

/**
 * A file with a confidentiality marking, stopped. What a member may know, as
 * facts: that there is a marking (not which, and not where), that nothing was
 * kept, and what the team's approvers are told -- that an upload was stopped,
 * when, and the kind of marking; not who sent it and not the file's name.
 */
export const CONFIDENTIAL_REFUSAL =
  "이 파일에는 기밀·대외비 표시가 있어 받지 않았습니다. 파일의 내용은 저장되지 않았습니다. " +
  "팀의 승인자에게는 업로드가 멈췄다는 사실과 그 시각, 표시의 종류만 알려지며 " +
  "올린 사람과 파일 이름은 알려지지 않습니다.";

/**
 * No answer that says what happened: a dropped connection, or a failure on
 * the server's side. A proxy can give up on a request the server finished,
 * so this does not claim nothing was stored -- the list, read again, says.
 */
export const UNANSWERED =
  "올린 결과를 확인하지 못했습니다. 목록에 자료가 보이지 않으면 저장되지 않은 것이니 다시 올려 주세요.";

/** Whether a failed upload may still have stored a row, so the list is worth reading again. */
export function mayHaveStored(cause: unknown): boolean {
  return !(cause instanceof ApiError) || cause.status >= 500;
}

/**
 * A team that already keeps as many rows as it may, links and uploads
 * together -- the sentence for it, or null for any other failure. By the
 * reason the server gives; its message is read too, for a server that sends
 * the refusal without one.
 */
export function shelfFull(cause: unknown, rules: MaterialUploadRules | null): string | null {
  if (!(cause instanceof ApiError) || cause.status !== 422) return null;
  const said = cause.details.reason === "shelf_full" || /at most \d+ materials/.test(cause.message);
  if (!said) return null;
  return rules
    ? `한 팀이 둘 수 있는 자료는 ${rules.max_materials}개까지입니다. 쓰지 않는 자료를 삭제한 뒤 다시 시도해 주세요.`
    : "팀이 둘 수 있는 자료 수를 넘었습니다. 쓰지 않는 자료를 삭제한 뒤 다시 시도해 주세요.";
}

/** What to tell the member whose upload failed. */
export function uploadRefusal(cause: unknown, rules: MaterialUploadRules | null): string {
  if (mayHaveStored(cause)) return UNANSWERED;
  const refused = cause as ApiError;
  if (refused.status === 413) return tooLarge(rules);
  if (refused.status === 404) {
    // Our own 404 is a team the reader is not in. The framework's, with no
    // code of ours, is a deployment where the upload route does not exist.
    return refused.code === "not_found"
      ? "이 팀의 자료를 볼 수 없습니다."
      : "이 서버에서는 파일 올리기를 쓰지 않습니다.";
  }
  if (refused.status !== 422) return "올리지 못했습니다. 잠시 후 다시 시도해 주세요.";
  if (refused.code === "confidential_file") return CONFIDENTIAL_REFUSAL;
  const full = shelfFull(refused, rules);
  if (full !== null) return full;
  if (refused.details.field === "title") {
    return (
      typedTextRefusal(refused) ??
      (rules
        ? `제목을 확인해 주세요. 비워 둘 수 없고 ${rules.max_title_chars}자까지 쓸 수 있습니다.`
        : "제목을 확인해 주세요.")
    );
  }
  if (refused.details.field === "file") {
    const reason = readReason(refused);
    if (reason === "unsupported_type") return wrongType(rules);
    if (reason === "too_large") return tooLarge(rules);
    if (reason !== null && reason in READ) return READ[reason as keyof typeof READ];
  }
  return "파일을 받지 못했습니다. 제목과 파일을 확인해 주세요.";
}

const NOT_READ: Record<MaterialNotRead, string> = {
  pictures: "그림",
  charts: "차트",
  embedded_files: "첨부된 파일",
};

/**
 * What a stored file held that was not read, for the member who just sent
 * it; null when everything was. The server says this once, in the answer to
 * the upload, so it is shown then and not on the row.
 */
export function notReadNote(kinds: readonly string[]): string | null {
  const named = [...new Set(kinds.map((kind) => NOT_READ[kind as MaterialNotRead]).filter(Boolean))];
  if (named.length === 0) return null;
  return `이 파일의 ${named.join(", ")} 안에 있는 글은 읽지 않았습니다. 그 글은 보관되지 않고 검색에도 나오지 않습니다.`;
}

/**
 * The day a row's masked text is deleted, where the viewer is; null for a
 * row that keeps nothing (a link). A day already reached reads as soon, not
 * as a date in the past: the task that deletes runs hourly.
 */
export function deletionDay(expiresAt: string | null, now: Date = new Date()): string | null {
  if (expiresAt === null) return null;
  const at = new Date(expiresAt);
  if (Number.isNaN(at.getTime())) return null;
  return at.getTime() <= now.getTime() ? "곧 삭제" : `${localToday(at)} 삭제 예정`;
}
