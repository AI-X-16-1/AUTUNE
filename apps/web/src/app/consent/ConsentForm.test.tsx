import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConsentForm } from "./ConsentForm";
import { missingConsents, REQUIRED_CONSENTS } from "../legal/consents";
import { LEGAL_DOCUMENTS } from "../legal/content";

// The consent page's rule (the user, 2026-10-02): a document has to be opened
// by the person's own click before it can be agreed to, and nothing is sent
// until every one of them is.

afterEach(cleanup);

const TWO = REQUIRED_CONSENTS.slice(0, 2);
const title = (document: string) => LEGAL_DOCUMENTS.find((d) => d.id === document)!.title;
const box = (document: string) =>
  screen.getByRole("checkbox", { name: new RegExp(title(document)) }) as HTMLInputElement;
const open = (document: string) => {
  const row = box(document).closest("li") as HTMLElement;
  fireEvent.click(row.querySelector("button") as HTMLButtonElement);
};
const submit = () => screen.getByRole("button", { name: "동의하고 계속" }) as HTMLButtonElement;

function form(onAgree = vi.fn(() => Promise.resolve()), onLeave = vi.fn()) {
  render(<ConsentForm required={TWO} onAgree={onAgree} onLeave={onLeave} />);
  return { onAgree, onLeave };
}

describe("ConsentForm", () => {
  it("does not let a document be agreed to before it was opened", () => {
    form();

    expect(TWO.map(({ document }) => box(document).disabled)).toEqual([true, true]);
    expect(screen.queryByRole("region")).toBeNull();
    expect(submit().disabled).toBe(true);
  });

  it("shows a document's own text when it is opened, and only then unlocks its box", () => {
    form();

    open("terms");

    expect(screen.getByRole("region", { name: title("terms") }).textContent).toContain("제1조");
    expect(box("terms").disabled).toBe(false);
    expect(box("privacy").disabled).toBe(true);
  });

  it("keeps a box unlocked after its document is closed again", () => {
    form();

    open("terms");
    open("terms");

    expect(screen.queryByRole("region")).toBeNull();
    expect(box("terms").disabled).toBe(false);
  });

  it("sends nothing until every document is agreed to, then all of them at once", async () => {
    const { onAgree } = form();

    open("terms");
    fireEvent.click(box("terms"));
    expect(submit().disabled).toBe(true);

    open("privacy");
    fireEvent.click(box("privacy"));
    fireEvent.click(submit());

    await waitFor(() => expect(onAgree).toHaveBeenCalledExactlyOnceWith(TWO));
  });

  it("has no single control that agrees to everything", () => {
    form();

    expect(screen.getAllByRole("checkbox")).toHaveLength(TWO.length);
    expect(screen.queryByText(/전체 동의|모두 동의합니다/)).toBeNull();
  });

  it("says so and stays on the page when the agreement could not be recorded", async () => {
    form(vi.fn(() => Promise.reject(new Error("503"))));
    for (const { document } of TWO) {
      open(document);
      fireEvent.click(box(document));
    }

    fireEvent.click(submit());

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("동의를 기록하지 못했습니다"),
    );
    expect(submit().disabled).toBe(false);
  });

  it("offers signing out as the way to leave without agreeing", () => {
    const { onAgree, onLeave } = form();

    fireEvent.click(screen.getByRole("button", { name: "동의하지 않고 로그아웃" }));

    expect(onLeave).toHaveBeenCalledOnce();
    expect(onAgree).not.toHaveBeenCalled();
  });
});

describe("what is still to be agreed to", () => {
  const all = REQUIRED_CONSENTS.map(({ document, version }) => ({ document, version }));

  it("is everything for a person with no record", () => {
    expect(missingConsents([])).toEqual(REQUIRED_CONSENTS);
  });

  it("is nothing once every required version is on record", () => {
    expect(missingConsents(all)).toEqual([]);
  });

  it("is a document again when the version on record is not the current one", () => {
    const stale = all.map((c) => (c.document === "terms" ? { ...c, version: "older" } : c));

    expect(missingConsents(stale).map((c) => c.document)).toEqual(["terms"]);
  });

  it("has a document to open for every required consent", () => {
    const ids = LEGAL_DOCUMENTS.map((doc) => doc.id as string);

    expect(REQUIRED_CONSENTS.every(({ document }) => ids.includes(document))).toBe(true);
  });
});
