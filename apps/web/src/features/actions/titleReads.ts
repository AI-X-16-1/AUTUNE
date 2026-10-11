/**
 * Reading again for the titles a run's rows are given after it (module B's
 * owner, 2026-10-09).
 *
 * A run marks itself done and only then queues the titles, as a task of their
 * own behind whatever the worker already holds (`title_meeting`). This tab
 * reads its rows the moment a run is done, so it drew them without titles and
 * went on doing so until the page was loaded again.
 *
 * So after a run the tab asks again at these moments, and only while a row
 * could still be given one. Three reads at most, over a minute: a title that
 * takes longer than that, like one that is never accepted, is there on the
 * next load of the page and costs nothing more here.
 */
export const TITLE_READS_MS = [10_000, 30_000, 60_000] as const;

interface Titled {
  id: string;
  origin: string;
  title?: string | null;
}

/**
 * Whether a row the pipeline wrote has no title yet. A row a person typed is
 * never given one, so it is nothing to wait for.
 */
export const awaitsTitle = (rows: readonly Titled[]): boolean =>
  rows.some((row) => row.origin === "model" && !row.title);

/**
 * `rows` with the titles `read` has for them, and nothing else of `read`.
 *
 * A status a person changed, a sentence they edited, a row added or removed
 * since `read` was asked for all stay as the screen has them: a list fetched a
 * moment before a click must not undo the click. A title is taken only for a
 * row that has none and still says the sentence the title is of. The same
 * array when no title came.
 */
export function withTitles<Row extends Titled>(
  rows: Row[],
  read: readonly Row[],
  sentence: (row: Row) => string,
): Row[] {
  const written = new Map(read.map((row) => [row.id, row]));
  let took = false;
  const next = rows.map((row) => {
    const now = written.get(row.id);
    if (row.title || !now?.title || sentence(now) !== sentence(row)) return row;
    took = true;
    return { ...row, title: now.title };
  });
  return took ? next : rows;
}
