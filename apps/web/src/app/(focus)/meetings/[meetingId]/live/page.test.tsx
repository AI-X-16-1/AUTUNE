import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LiveMeetingPage from "./page";

// The route joins the live gate (module A's) to the notice of #392's operating
// rule (module B's), and the live screen's end-of-meeting slot to module C's
// band (#1147): the features do not import each other. What matters here is
// only the joining.

vi.mock("@/features/actions", () => ({
  OwnTeamMeetingsNotice: ({
    entrance,
    className,
  }: {
    entrance: string;
    className?: string;
  }) => (
    <p role="note" className={className}>
      the notice of module B, {entrance}
    </p>
  ),
}));
vi.mock("@/features/gap", () => ({
  EndAlertBand: ({
    teamId,
    exceptMeetingId,
  }: {
    teamId: string;
    exceptMeetingId?: string;
  }) => (
    <aside>
      the band of module C, {teamId}, without {exceptMeetingId}
    </aside>
  ),
}));
vi.mock("@/features/transcript", () => ({
  LiveMeetingScreen: ({
    meetingId,
    notice,
    endingSoon,
  }: {
    meetingId: string;
    notice?: ReactNode;
    endingSoon?: (teamId: string) => ReactNode;
  }) => (
    <div>
      <output>{meetingId}</output>
      {notice}
      {/* The screen decides when; here it is always, with a team of its own. */}
      {endingSoon?.("team_of_the_meeting")}
    </div>
  ),
}));

afterEach(cleanup);

describe("the live meeting route", () => {
  it("hands the notice of module B to the slot of the gate, with the gate's spacing", async () => {
    render(
      await LiveMeetingPage({ params: Promise.resolve({ meetingId: "mtg_1" }) }),
    );

    const note = screen.getByRole("note");
    expect(note.textContent).toBe("the notice of module B, live");
    expect(note.className).toBe("mt-6");
    expect(screen.getByRole("status").textContent).toBe("mtg_1");
  });

  it("fills the end-of-meeting slot with the band of module C, asked by the team the screen names", async () => {
    render(
      await LiveMeetingPage({ params: Promise.resolve({ meetingId: "mtg_1" }) }),
    );

    expect(screen.getByRole("complementary").textContent).toBe(
      "the band of module C, team_of_the_meeting, without mtg_1",
    );
  });
});
