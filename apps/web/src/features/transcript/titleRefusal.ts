import { ApiError } from "@/shared/api/client";

/**
 * A meeting title module A refused to store (#1130, #1161).
 *
 * The server screens a title when a meeting is opened and when it is renamed,
 * and answers a 422 whose details say `reason: "personal_data"` and which
 * kinds of value it read -- never the value. The sentence here says what to
 * take out; the title the person typed stays in the field, so they fix it and
 * save again.
 *
 * The same sentence module B's screens show for the same refusal. Written
 * here and not imported: a feature does not import another (`CLAUDE.md` in
 * this folder).
 */
const KINDS: Record<string, string> = {
  phone: "전화번호",
  email: "이메일 주소",
  rrn: "주민등록번호",
  card: "카드 번호",
  account: "계좌번호",
  digits: "긴 번호",
};

/** What to show for a title refused as personal data, or null for any other failure. */
export function titleRefusal(cause: unknown): string | null {
  if (!(cause instanceof ApiError) || cause.status !== 422) return null;
  if (cause.details.reason !== "personal_data") return null;
  const read = Array.isArray(cause.details.categories) ? cause.details.categories : [];
  // The server names each kind once (`find_unmasked`).
  const kinds = read.map((kind) => KINDS[String(kind)]).filter(Boolean);
  // Every name above ends in a vowel, as 개인정보 does: "…로 보이는".
  const what = kinds.length > 0 ? kinds.join(", ") : "개인정보";
  return `${what}로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.`;
}

/** What to show when a rename did not go through. */
export function renameRefusal(cause: unknown): string {
  const personal = titleRefusal(cause);
  if (personal !== null) return personal;
  if (cause instanceof ApiError) {
    if (cause.status === 403) return "이 회의의 이름을 바꿀 수 없습니다.";
    if (cause.status === 404) return "회의를 찾을 수 없습니다.";
    if (cause.status === 422) return "이름은 1자 이상 400자 이하로 적어 주세요.";
  }
  return "이름을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.";
}
