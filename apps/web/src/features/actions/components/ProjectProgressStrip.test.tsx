import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectProgressStrip } from "./ProjectProgressStrip";
import type { Project } from "../types";

// The team board's progress strip (the user, 2026-10-04).

const A = { id: "prj_a", name: "알파", aliases: [] } as unknown as Project;

afterEach(cleanup);

describe("ProjectProgressStrip", () => {
  it("is not drawn without a line", () => {
    const { container } = render(
      <ProjectProgressStrip lines={[]} value="all" onChoose={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows done, late and the share done", () => {
    render(
      <ProjectProgressStrip
        lines={[{ project: A, total: 4, done: 1, overdue: 2 }]}
        value="all"
        onChoose={() => {}}
      />,
    );
    expect(screen.getByText(/완료 1\/4/)).toBeTruthy();
    expect(screen.getByText(/기한 초과 2/)).toBeTruthy();
    expect(
      screen
        .getByRole("progressbar", { name: "알파 완료율" })
        .getAttribute("aria-valuenow"),
    ).toBe("25");
  });

  it("filters to the project, and clears on a second choice", () => {
    const onChoose = vi.fn();
    const line = { project: A, total: 1, done: 1, overdue: 0 };
    const { rerender } = render(
      <ProjectProgressStrip lines={[line]} value="all" onChoose={onChoose} />,
    );
    expect(screen.queryByText(/기한 초과/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /알파/ }));
    expect(onChoose).toHaveBeenLastCalledWith("prj_a");
    rerender(
      <ProjectProgressStrip lines={[line]} value="prj_a" onChoose={onChoose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /알파/ }));
    expect(onChoose).toHaveBeenLastCalledWith("all");
  });
});
