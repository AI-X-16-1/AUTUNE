import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NewMeetingPage from "./page";

// The route joins the upload form (module A's) to the notice of #392's
// operating rule (module B's): the two features do not import each other.
// What matters here is only the joining.

vi.mock("@/features/actions", () => ({
  OwnTeamMeetingsNotice: ({ entrance }: { entrance: string }) => (
    <p role="note">the notice of module B, {entrance}</p>
  ),
}));
vi.mock("@/features/transcript", () => ({
  NewMeetingScreen: ({
    existingMeetingId,
    notice,
  }: {
    existingMeetingId?: string;
    notice?: ReactNode;
  }) => (
    <div>
      <output>{existingMeetingId ?? "a new meeting"}</output>
      {notice}
    </div>
  ),
}));

afterEach(cleanup);

describe("the 새 회의 route", () => {
  it("hands the notice of module B to the slot of the form", async () => {
    render(await NewMeetingPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("note").textContent).toBe(
      "the notice of module B, upload",
    );
    expect(screen.getByRole("status").textContent).toBe("a new meeting");
  });

  it("hands it on a re-upload too, where the form is only the file and consent", async () => {
    render(
      await NewMeetingPage({ searchParams: Promise.resolve({ meeting: "mtg_1" }) }),
    );

    expect(screen.getByRole("note").textContent).toBe(
      "the notice of module B, upload",
    );
    expect(screen.getByRole("status").textContent).toBe("mtg_1");
  });
});
