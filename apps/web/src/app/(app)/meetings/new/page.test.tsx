import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NewMeetingPage from "./page";

// The route joins the upload form (module A's) to the notice of #392's
// operating rule (module B's), and its agenda row to what modules B, C and D
// have to draft from (#1147): the features do not import each other. What
// matters here is only the joining.

vi.mock("@/features/actions", () => ({
  OwnTeamMeetingsNotice: ({ entrance }: { entrance: string }) => (
    <p role="note">the notice of module B, {entrance}</p>
  ),
  JIRA_AGENDA: { label: "B's Jira issues", lines: () => Promise.resolve([]) },
}));
vi.mock("@/features/context", () => ({
  DECISIONS_AGENDA: { label: "D's decisions", lines: () => Promise.resolve([]) },
}));
vi.mock("@/features/gap", () => ({
  OPEN_GAPS_AGENDA: { label: "C's open gaps", lines: () => Promise.resolve([]) },
}));
vi.mock("@/features/transcript", () => ({
  NewMeetingScreen: ({
    existingMeetingId,
    notice,
    agendaSources,
  }: {
    existingMeetingId?: string;
    notice?: ReactNode;
    agendaSources?: readonly { label: string }[];
  }) => (
    <div>
      <output>{existingMeetingId ?? "a new meeting"}</output>
      {notice}
      <ol>
        {agendaSources?.map((source) => (
          <li key={source.label}>{source.label}</li>
        ))}
      </ol>
    </div>
  ),
}));

afterEach(cleanup);

describe("the 새 회의 route", () => {
  it("lists what the agenda row may draft from: an integration, then earlier meetings", async () => {
    render(await NewMeetingPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "B's Jira issues",
      "D's decisions",
      "C's open gaps",
    ]);
  });

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
