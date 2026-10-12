import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REQUIRED_CONSENTS } from "../legal/consents";
import { SessionGate } from "./SessionGate";

// The gate in front of every screen: who gets in, and who is sent to agree to
// the documents first. The server records consent and refuses nothing, so this
// is the only place the rule is applied.

const replace = vi.fn();
const getSession = vi.fn();
const getConsents = vi.fn();
const authHeaders = vi.fn((): Record<string, string> => ({}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/meetings/m1/actions",
  useRouter: () => ({ replace }),
}));
vi.mock("@/shared/api/auth", () => ({
  getSession: () => getSession(),
  getConsents: () => getConsents(),
}));
vi.mock("@/shared/api/client", () => ({
  authHeaders: () => authHeaders(),
  setSignedIn: vi.fn(),
}));

const ME = { id: "user_me", email: "me@example.com", display_name: "Me", teams: [] };
const EVERYTHING = REQUIRED_CONSENTS.map(({ document, version }) => ({ document, version }));

beforeEach(() => {
  window.history.replaceState(null, "", "/meetings/m1/actions?tab=mine");
  authHeaders.mockReturnValue({});
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const gate = () =>
  render(
    <SessionGate>
      <p>the app</p>
    </SessionGate>,
  );

describe("SessionGate", () => {
  it("opens the app for a person who agreed to every required document", async () => {
    getSession.mockResolvedValue(ME);
    getConsents.mockResolvedValue(EVERYTHING);

    gate();

    await waitFor(() => expect(screen.getByText("the app")).toBeTruthy());
    expect(replace).not.toHaveBeenCalled();
  });

  it("sends a person with a consent missing to the consent page, and remembers where they were", async () => {
    getSession.mockResolvedValue(ME);
    getConsents.mockResolvedValue(EVERYTHING.slice(1));

    gate();

    await waitFor(() =>
      expect(replace).toHaveBeenCalledExactlyOnceWith(
        `/consent?next=${encodeURIComponent("/meetings/m1/actions?tab=mine")}`,
      ),
    );
    expect(screen.queryByText("the app")).toBeNull();
  });

  it("asks again when a document's version on record is not the current one", async () => {
    getSession.mockResolvedValue(ME);
    getConsents.mockResolvedValue(EVERYTHING.map((c, i) => (i === 0 ? { ...c, version: "older" } : c)));

    gate();

    await waitFor(() => expect(replace).toHaveBeenCalledOnce());
  });

  it("does not hold the app shut when the record cannot be read", async () => {
    getSession.mockResolvedValue(ME);
    getConsents.mockResolvedValue(null);

    gate();

    await waitFor(() => expect(screen.getByText("the app")).toBeTruthy());
    expect(replace).not.toHaveBeenCalled();
  });

  it("still sends somebody with no session to sign in, without asking about consent", async () => {
    getSession.mockResolvedValue(null);

    gate();

    await waitFor(() => expect(replace).toHaveBeenCalledExactlyOnceWith("/login"));
    expect(getConsents).not.toHaveBeenCalled();
  });

  it("lets a developer token through with no person to ask", async () => {
    getSession.mockResolvedValue(null);
    authHeaders.mockReturnValue({ authorization: "Bearer dev" });

    gate();

    await waitFor(() => expect(screen.getByText("the app")).toBeTruthy());
    expect(getConsents).not.toHaveBeenCalled();
  });
});
