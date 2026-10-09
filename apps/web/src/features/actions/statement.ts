import { writtenDay } from "./dates";

/**
 * A decision's sentence as these screens show it (the user, 2026-10-09).
 *
 * The server ends a decision that set a deadline with it -- "… (담당 박지영,
 * 기한 2026-10-13)" -- and on a page whose other dates read "10월 13일 (화)"
 * that one read as another kind of date. Here it is written the page's way.
 *
 * **Only what is shown.** The stored sentence keeps its form: the Meeting
 * Context Engine compares it with the same decision's earlier wording, and it
 * is the text the team's Notion was sent. So the box a sentence is reworded in
 * opens with the stored sentence, not with this one -- what is saved there is
 * stored.
 *
 * **Only the deadline that closes the sentence.** That is where the server
 * writes it. A date somebody said inside the sentence stays as it was said.
 *
 * `year` is the one the reader takes for granted -- the page's own where it
 * has a date line, this year on a row that has none -- and is the only year
 * not written.
 */
export function shownStatement(
  statement: string,
  year: number | null = new Date().getFullYear(),
): string {
  return statement.replace(DEADLINE, (whole, day: string) => {
    const written = writtenDay(day, year);
    return written ? `기한 ${written})` : whole;
  });
}

const DEADLINE = /기한 (\d{4}-\d{2}-\d{2})\)$/;
