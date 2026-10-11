import { ApiError } from "@/shared/api/client";

/**
 * Text a person typed that module B refused to store (#1130).
 *
 * The server screens typed text before it is stored and answers a 422 whose
 * details say `reason: "personal_data"` and which kinds of value it read --
 * never the value. The sentence here says what to take out, which is what the
 * privacy owner asked of the message; the text the person typed stays in the
 * field, so they fix it and save again.
 */
const KINDS: Record<string, string> = {
  phone: "전화번호",
  email: "이메일 주소",
  rrn: "주민등록번호",
  card: "카드 번호",
  account: "계좌번호",
  digits: "긴 번호",
};

/** What to show for a save refused as personal data, or null for any other failure. */
export function typedTextRefusal(cause: unknown): string | null {
  if (!(cause instanceof ApiError) || cause.status !== 422) return null;
  if (cause.details.reason !== "personal_data") return null;
  const read = Array.isArray(cause.details.categories) ? cause.details.categories : [];
  const kinds = [...new Set(read.map((kind) => KINDS[String(kind)]).filter(Boolean))];
  // Every name above ends in a vowel, as 개인정보 does: "…로 보이는".
  const what = kinds.length > 0 ? kinds.join(", ") : "개인정보";
  return `${what}로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.`;
}
