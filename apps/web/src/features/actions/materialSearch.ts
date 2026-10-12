import { ApiError } from "@/shared/api/client";

import type { MaterialUploadRules } from "./types";

/**
 * The longest question the server takes (`MaterialQuestion` in
 * `modules/extraction/src/autune_extraction/schemas.py`), for a rules answer
 * that does not carry the number.
 */
export const MAX_QUESTION_CHARS = 300;

/**
 * The longest question this server takes: its own number where it gave one.
 * A longer question is a 422; the box stops there, and the refusal is worded
 * all the same.
 */
export function questionLimit(rules: MaterialUploadRules): number {
  const said = rules.max_question_chars;
  return typeof said === "number" && said > 0 ? said : MAX_QUESTION_CHARS;
}

/** What to tell the member whose question got no answer. */
export function searchRefusal(cause: unknown, limit: number): string {
  if (cause instanceof ApiError) {
    if (cause.status === 404) {
      // Ours, for somebody not on the team; any other 404 is a server on
      // which uploads -- and so the search over them -- are off.
      return cause.code === "not_found"
        ? "이 팀의 자료를 볼 수 없습니다."
        : "이 서버에서는 올린 파일에서 찾기를 쓰지 않습니다.";
    }
    if (cause.status === 422) {
      return `질문은 ${limit}자까지 적을 수 있습니다. 줄여서 다시 찾아 주세요.`;
    }
  }
  return "찾지 못했습니다. 잠시 후 다시 시도해 주세요.";
}
