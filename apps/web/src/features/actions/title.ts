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
 * one, the cut moves back to before it. The same for a count in other words
 * and a unit set apart ("스무 명", "3 개월", "30 분"), and for the word that
 * makes a point in time of an amount or a bound of a count ("3일 전에", "이번
 * 주 안에", "50명 이상"). A first word too long to fit -- a
 * sentence with no space in it -- is cut at the limit, moved back out of a
 * number if it lands in one.
 *
 * Nor inside a masked span (#1072 review). Module A hides personal data by
 * keeping its shape and writing "*" for its content (`autune_audio.masking`):
 * "010-****-5678", "k***@example.com", "김**" -- and a number said with spaces
 * keeps them, "010 **** 5678", "**** **** **** 3456", which is where a cut
 * between words would land. Half of one no longer says what was hidden. This
 * is about reading, not exposure: the text arrives masked. There is no
 * bracketed mark to keep whole: "[사람N]" exists only in a request to a model
 * and is put back before a sentence is stored.
 */
export const TITLE_MAX = 20;

const ELLIPSIS = "…";

const RELATIVE = "(?:다음|이번|지난|저번|오는|매)\\s*(?:주|달|분기|해)";
const DAY = "(?:\\d+년\\s*)?\\d+월\\s*\\d+일";
const CLOCK = "\\d+시(?:\\s*\\d+분|\\s*반)?";
const WEEKDAY = "[월화수목금토일]요일";
const NEAR = "오늘|내일|모레|어제|오전|오후|올해|내년|작년|주말|월말|연말|당일";
/** A number of days that is one word: "이틀 안에" is cut no sooner than "2일 안에". */
const SPELLED = "하루|이틀|사흘|나흘|닷새|열흘|보름";
/** A unit that seldom starts another word, so the start of a word is enough:
 * "3 개월", "200 명한테". A word that only starts like one ("200 개발자") is
 * kept with the number too: that moves a cut earlier and never into one. */
const OPEN_UNIT =
  "번|개|명|건|차례|가지|군데|시간|분기|사분기|주|달|년|원|퍼센트|곳|쪽|페이지|사람";
/** A unit that is also how everyday words here begin -- 일정, 분석, 초안,
 * 회의, 배포, 장애, 대응, 팀장, 해결, 프로젝트 -- and so counts only when the
 * word ends with it or goes on with a particle: "3 일", "30 분 동안", "두
 * 배로", and not "버전 2 배포". "의" is left out of the particles for "회의". */
const CLOSED_UNIT =
  "(?:일|분|초|회|배|장|대|팀|해|프로)" +
  "(?:(?![가-힣])|(?=간|동안|째|씩|쯤|마다|까지|부터|에|은|는|이|가|을|를|도|만|으로|로|밖에))";
const UNIT = `(?:${OPEN_UNIT}|${CLOSED_UNIT})`;
/** A count in words, "한 번", "스무 명", "열두 개", "두세 군데", "몇 번": only
 * at the start of a word, or "중요한 번역" is one. The numbers Korean counts
 * with, not the ones it reads digits with: "이 주" is "this week" as often as
 * "two weeks", and nothing here can tell. */
const ONES = "한|두|세|네|다섯|여섯|일곱|여덟|아홉";
const TENS = "열|스물|서른|마흔|쉰|예순|일흔|여든|아흔";
const ROUGHLY = "한두|두세|서너|두어|네댓|스무|석|넉|몇|여러|수십|수백|첫";
const HOW_MANY = `(?:(?:${TENS})(?:${ONES})|${ROUGHLY}|${ONES}|${TENS})`;
const COUNTED = `(?<![가-힣])${HOW_MANY}\\s+${UNIT}\\S*`;
/** A number with what follows it, and its unit when a space parts them. */
const NUMBER = `\\d\\S*(?:\\s+${UNIT}\\S*)?`;
/** What turns an amount of time into a point in it, or a count into a bound:
 * "3일 전에", "2주 뒤", "이번 주 안에", "금요일 전까지", "50명 이상", "3명 중
 * 2명". Without it "출시 3일…" reads as the third day. A word of its own, the
 * particle after it at most: "3일 전체 회의" and "5개 안건" are not this. It
 * only follows a date or a number, so "공지 뒤 5일" may still be cut after
 * "뒤". */
const SPAN =
  "(?:전|후|뒤|안|내|이내|중|이상|이하|미만|초과|동안)" +
  "(?:에|까지|부터|으로|로|은|는|이|의|쯤|만|도)*(?![가-힣])";
/** The one such word that has a number on both sides: "3명 중 2명". */
const AMONG = "중(?:에서|에)?";
/** A word module A masked: any word with a "*" in it. One "*" is enough -- a
 * two-character name is hidden as "김*". The look-behind only saves time. */
const MASKED = "(?<!\\S)\\S*\\*\\S*";
const PART = `(?:${MASKED}|${RELATIVE}|${DAY}|${CLOCK}|${WEEKDAY}|${NEAR}|${SPELLED}|${COUNTED}|${NUMBER})`;
/** A date, a time, numbers or masked words in a row: parts with only spaces
 * between them, so "010 **** 5678" is one -- and the word after the run that
 * says before, after, within or more than it, which ends it. */
const UNBROKEN = new RegExp(`${PART}(?:\\s+(?:${AMONG}\\s+)?${PART})*(?:\\s+${SPAN})?`, "g");
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

/** A masked span inside a word with no space around it: the "*" run with the
 * digits, Latin letters and separators the masker leaves beside it. */
const MASKED_IN_WORD = /[\w@+\-.–—)]*\*[\w@+\-.–—)*]*/g;

/** The first `room` characters, moved back out of a masked span or a number
 * it ends inside. */
function hardCut(text: string, room: number): string {
  const letters = [...text];
  let end = room;
  for (const match of text.matchAll(MASKED_IN_WORD)) {
    let start = count(text.slice(0, match.index));
    const stop = start + count(match[0]);
    // A name keeps its first character, which is no letter this pattern
    // takes: "김**" starts one before its first "*".
    if (match[0].startsWith("*") && start > 0) start -= 1;
    if (start > 0 && start < end && end < stop) end = start;
  }
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

/**
 * The top line of a row that may have a title of its own (module B's owner,
 * 2026-10-09): the summary the server wrote for the sentence -- twenty
 * characters or fewer, ended by a noun -- or, when it has none, the sentence
 * cut as above. Most rows have none: a sentence a person typed or edited,
 * one whose summary was refused, every row from before.
 *
 * A summary stands for a sentence that says more, so `cut` is true for it and
 * the row still leads to the whole sentence. It is never cut again here; one
 * longer than `TITLE_MAX` is not a title the server keeps, and is not shown.
 */
export function rowTitle(
  title: string | null | undefined,
  sentence: string,
): ShownTitle {
  const written = title?.trim();
  if (!written || count(written) > TITLE_MAX) return shortTitle(sentence);
  return { shown: written, cut: written !== sentence.trim() };
}
