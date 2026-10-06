import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntegrationSettingsScreen } from "./IntegrationSettingsScreen";

// 설정 › 연동 opens on the team chosen elsewhere in the app, follows a new
// choice while it is open, and tells the route when a team is picked here
// (the user, 2026-10-06). The route joins it to the feature that keeps the
// choice; here that is two props.

const session = vi.fn();
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./DueReminderSetting", () => ({ DueReminderSetting: () => null }));
vi.mock("./NotificationPauseSetting", () => ({ NotificationPauseSetting: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectSettings", () => ({ ProjectSettings: () => null }));
vi.mock("./SlackConnect", () => ({
  SlackConnect: ({ teamId }: { teamId: string }) => <div data-testid="team">{teamId}</div>,
}));

const TEAMS = [
  { id: "team_a", name: "가 팀" },
  { id: "team_b", name: "나 팀" },
];
const me = (teams = TEAMS) => ({ id: "user_me", email: "me@example.com", display_name: "Me", teams });
const team = () => screen.getByTestId("team").textContent;
const found = async () => (await screen.findByTestId("team")).textContent;
const picker = () => screen.getByRole("combobox") as HTMLSelectElement;

/**
 * What the route does: hold the team chosen in the app, hand it to the screen,
 * and take a pick made on the screen as that choice. The button is the
 * sidebar's menu choosing a team while the screen is open.
 */
function Route({ onChoose }: { onChoose?: (teamId: string) => void }) {
  const [chosen, setChosen] = useState<string | null>(null);
  return (
    <>
      <button type="button" onClick={() => setChosen("team_a")}>
        sidebar picks 가 팀
      </button>
      <IntegrationSettingsScreen
        chosenTeamId={chosen}
        onChooseTeam={(teamId) => {
          onChoose?.(teamId);
          setChosen(teamId);
        }}
      />
    </>
  );
}

afterEach(() => {
  cleanup();
  session.mockReset();
});

describe("IntegrationSettingsScreen, the team it is about", () => {
  it("opens on the team chosen elsewhere", async () => {
    session.mockResolvedValue(me());

    render(<IntegrationSettingsScreen chosenTeamId="team_b" />);

    expect(await found()).toBe("team_b");
    expect(picker().value).toBe("team_b");
  });

  it("opens on the first team when none was chosen, or the chosen one is not theirs", async () => {
    session.mockResolvedValue(me());
    const first = render(<IntegrationSettingsScreen />);
    expect(await found()).toBe("team_a");
    first.unmount();

    render(<IntegrationSettingsScreen chosenTeamId="team_left" />);
    expect(await found()).toBe("team_a");
  });

  it("follows a team chosen elsewhere while it is open", async () => {
    session.mockResolvedValue(me());
    const view = render(<IntegrationSettingsScreen chosenTeamId="team_a" />);
    expect(await found()).toBe("team_a");

    view.rerender(<IntegrationSettingsScreen chosenTeamId="team_b" />);

    await waitFor(() => expect(team()).toBe("team_b"));
  });

  it("tells the route when a team is picked here, and shows the team the route hands back", async () => {
    session.mockResolvedValue(me());
    const chose = vi.fn();
    render(<Route onChoose={chose} />);
    await found();

    fireEvent.change(picker(), { target: { value: "team_b" } });

    expect(team()).toBe("team_b");
    expect(chose).toHaveBeenCalledExactlyOnceWith("team_b");
  });

  it("a team picked here does not outlive a later choice in the sidebar", async () => {
    // mkkim68, review of #883: pick X in the select, then Y in the sidebar
    // with the screen still open. The screen went on showing X while the
    // sidebar marked Y, because the select's own pick was kept and won.
    session.mockResolvedValue(me());
    render(<Route />);
    expect(await found()).toBe("team_a");

    fireEvent.change(picker(), { target: { value: "team_b" } }); // X, here
    expect(team()).toBe("team_b");
    fireEvent.click(screen.getByRole("button", { name: "sidebar picks 가 팀" })); // Y, there

    expect(team()).toBe("team_a");
    expect(picker().value).toBe("team_a");
  });

  it("says under the connections where what they send is written down", async () => {
    session.mockResolvedValue(me());
    render(<IntegrationSettingsScreen />);
    await found();

    const link = screen.getByRole("link", { name: "개인정보 처리방침" });
    expect(link.getAttribute("href")).toBe("/legal#privacy");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.parentElement?.textContent).toContain("제5조(제3자 제공)와 제7조(국외 이전)");
  });

  it("holds its own pick when no route is listening", async () => {
    // The two props are optional: mounted bare, the select is all there is.
    session.mockResolvedValue(me());
    render(<IntegrationSettingsScreen />);
    expect(await found()).toBe("team_a");

    fireEvent.change(picker(), { target: { value: "team_b" } });

    expect(team()).toBe("team_b");
    expect(picker().value).toBe("team_b");
  });
});
