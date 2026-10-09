import { describe, expect, it } from "vitest";

import { awaitsTitle, TITLE_READS_MS, withTitles } from "./titleReads";

// A run's rows get their titles from a task that runs after the run is done
// (module B's owner, 2026-10-09). What is waited for, and what a later read
// may change on the screen: a title, and nothing else.

interface Row {
  id: string;
  origin: string;
  title?: string | null;
  text: string;
  status?: string;
}

const row = (id: string, over: Partial<Row> = {}): Row => ({
  id,
  origin: "model",
  title: null,
  text: `${id}의 문장`,
  ...over,
});
const text = (r: Row) => r.text;

describe("awaitsTitle", () => {
  it("waits for a row the pipeline wrote that has no title", () => {
    expect(awaitsTitle([row("a", { title: "제목" }), row("b")])).toBe(true);
    // A server from before titles sends no key at all.
    expect(awaitsTitle([{ id: "c", origin: "model" }])).toBe(true);
  });

  it("waits for nothing when every such row has one, or there are no rows", () => {
    expect(awaitsTitle([row("a", { title: "제목" })])).toBe(false);
    expect(awaitsTitle([])).toBe(false);
  });

  it("does not wait for a row a person typed, which is given none", () => {
    expect(
      awaitsTitle([
        row("a", { origin: "user" }),
        row("b", { origin: "chat" }),
        row("c", { origin: "followup" }),
      ]),
    ).toBe(false);
  });
});

describe("withTitles", () => {
  it("takes the title written for a row that had none", () => {
    const rows = [row("a"), row("b")];

    const next = withTitles(rows, [row("a", { title: "보고서 정리" }), row("b")], text);

    expect(next.map((r) => r.title)).toEqual(["보고서 정리", null]);
    expect(next[1]).toBe(rows[1]);
  });

  it("takes nothing else of the read: a change made on the screen meanwhile stays", () => {
    const rows = [row("a", { status: "confirmed" })];

    const next = withTitles(rows, [row("a", { title: "보고서 정리", status: "pending" })], text);

    expect(next).toEqual([row("a", { title: "보고서 정리", status: "confirmed" })]);
  });

  it("does not take a title of another sentence than the row now says", () => {
    const rows = [row("a", { text: "고쳐 쓴 문장" })];

    expect(withTitles(rows, [row("a", { title: "보고서 정리" })], text)).toBe(rows);
  });

  it("does not write over a title the row already has", () => {
    const rows = [row("a", { title: "보고서 정리" })];

    expect(withTitles(rows, [row("a", { title: "다른 제목" })], text)).toBe(rows);
  });

  it("neither adds a row the screen does not have nor drops one the read lacks", () => {
    const rows = [row("a"), row("gone")];

    const next = withTitles(rows, [row("a", { title: "보고서 정리" }), row("new")], text);

    expect(next.map((r) => r.id)).toEqual(["a", "gone"]);
  });

  it("is the same array when no title came", () => {
    const rows = [row("a"), row("b", { title: "제목" })];

    expect(withTitles(rows, [row("a"), row("b", { title: "제목" })], text)).toBe(rows);
  });
});

describe("TITLE_READS_MS", () => {
  it("is three reads within a minute, in order", () => {
    expect([...TITLE_READS_MS]).toEqual([10_000, 30_000, 60_000]);
  });
});
