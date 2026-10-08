/**
 * The top line of an action card and of a decision row, as shown: at most
 * `TITLE_MAX` characters (the user, 2026-10-08).
 *
 * A way of showing, not a summary: the stored sentence is untouched -- it is
 * what the detail window, the 요약 tab, the copied minutes, every message and
 * every other module read -- and only the board cuts it. So nothing is
 * rewritten here and no word is added; the end is left off and "…" says so.
 *
 * Counting: every character is one -- a letter, a space, a digit, a mark --
 * and the "…" is inside the limit, so a cut line is at most `TITLE_MAX` long
 * with it.
 *
 * Where it is cut: between words, at the last gap that still fits, and never
 * inside a date, a count or a run of numbers ("10월 15일 오후 3시", "다음 주
 * 화요일", "한 번", "200 명"): half a date reads as another date, and a count
 * without what it counts as another amount. When the gap that fits would fall inside
 * one, the cut moves back to before it. A first word too long to fit -- a
 * sentence with no space in it -- is cut at the limit, moved back out of a
 * number if it lands in one.
 */
export const TITLE_MAX = 20;

const ELLIPSIS = "…";

const RELATIVE = "(?:다음|이번|지난|오는|매)\\s*(?:주|달|분기|해)";
const DAY = "(?:\\d+년\\s*)?\\d+월\\s*\\d+일";
const CLOCK = "\\d+시(?:\\s*\\d+분|\\s*반)?";
const WEEKDAY = "[월화수목금토일]요일";
const NEAR = "오늘|내일|모레|오전|오후";
const UNIT = "(?:번|개|명|건|차례|가지|군데|시간|주|달|원|퍼센트)";
/** A count in words, "한 번": only at the start of a word, or "중요한 번역" is one. */
const COUNTED = `(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\\s+${UNIT}`;
/** A number with what follows it, and its unit when a space parts them. A
 * word that only starts like a unit ("200 개발자") is kept with the number
 * too: that moves a cut earlier and never into one. */
const NUMBER = `\\d\\S*(?:\\s+${UNIT})?`;
const PART = `(?:${RELATIVE}|${DAY}|${CLOCK}|${WEEKDAY}|${NEAR}|${COUNTED}|${NUMBER})`;
/** A date, a time or numbers in a row: parts with only spaces between them. */
const UNBROKEN = new RegExp(`${PART}(?:\\s+${PART})*`, "g");
/** What a cut leaves dangling at the end of the kept part. */
const DANGLING = /[\s,.;:·\-–—([]+$/u;

export interface ShownTitle {
  shown: string;
  /** The sentence is longer than what is shown. */
  cut: boolean;
}

function count(text: string): number {
  return [...text].length;
}

/** The gaps a cut may fall in: the start of each run of spaces outside every
 * date and number run. */
function gaps(text: string): number[] {
  const closed: Array<[number, number]> = [];
  for (const match of text.matchAll(UNBROKEN)) {
    closed.push([match.index, match.index + match[0].length]);
  }
  const open: number[] = [];
  for (const match of text.matchAll(/\s+/g)) {
    const at = match.index;
    if (!closed.some(([start, end]) => start < at && at < end)) open.push(at);
  }
  return open;
}

/** The first `room` characters, moved back out of a number it ends inside. */
function hardCut(text: string, room: number): string {
  const letters = [...text];
  let end = room;
  if (/\d/.test(letters[end] ?? "") && /[\d,.]/.test(letters[end - 1] ?? "")) {
    let start = end;
    while (start > 0 && /[\d,.]/.test(letters[start - 1] ?? "")) start -= 1;
    if (start > 0) end = start;
  }
  return letters.slice(0, end).join("");
}

export function shortTitle(sentence: string, max: number = TITLE_MAX): ShownTitle {
  const text = sentence.trim();
  if (count(text) <= max) return { shown: text, cut: false };
  const room = max - count(ELLIPSIS);
  let kept = "";
  for (const at of gaps(text)) {
    const head = text.slice(0, at).replace(DANGLING, "");
    if (count(head) > room) break;
    kept = head;
  }
  if (kept === "") kept = hardCut(text, room).replace(DANGLING, "");
  return { shown: kept + ELLIPSIS, cut: true };
}
