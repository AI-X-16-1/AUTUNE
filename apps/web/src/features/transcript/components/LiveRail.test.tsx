import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LiveRail } from "./LiveRail";

// 녹음 종료 waits seconds for the last row; the button has to show it heard.

afterEach(cleanup);

describe("LiveRail", () => {
  it("offers pause and stop while recording", () => {
    render(
      <LiveRail state="recording" elapsedSeconds={10} levels={[]} onPause={vi.fn()} onStop={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "일시정지" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "녹음 종료" })).toBeTruthy();
  });

  it("shows the stop is under way and takes no second press", () => {
    render(
      <LiveRail
        state="recording"
        elapsedSeconds={10}
        levels={[]}
        onPause={vi.fn()}
        onStop={vi.fn()}
        stopping
      />,
    );

    const stop = screen.getByRole("button", { name: "마무리하는 중…" }) as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    expect(stop.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByRole("button", { name: "일시정지" })).toBeNull();
  });
});
