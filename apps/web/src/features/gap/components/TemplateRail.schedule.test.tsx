import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TemplateRail } from "./TemplateRail";
import { DEMO_COMPARISON } from "../fixtures/report-demo";

// "다음 회의 잡기" (#824): beside the rail's heading, for the whole meeting.

afterEach(cleanup);

const button = () => screen.getByRole("button", { name: "다음 회의 잡기" }) as HTMLButtonElement;

describe("TemplateRail — 다음 회의 잡기", () => {
  it("sends the meeting's gaps on when pressed", () => {
    const onScheduleNext = vi.fn();
    render(<TemplateRail comparison={DEMO_COMPARISON} onScheduleNext={onScheduleNext} />);

    fireEvent.click(button());

    expect(onScheduleNext).toHaveBeenCalledOnce();
  });

  it("is not drawn without its handler", () => {
    render(<TemplateRail comparison={DEMO_COMPARISON} />);

    expect(screen.queryByRole("button", { name: "다음 회의 잡기" })).toBeNull();
  });

  it("waits for a meeting that has been compared", () => {
    render(
      <TemplateRail
        comparison={{ ...DEMO_COMPARISON, analysed: false }}
        onScheduleNext={vi.fn()}
      />,
    );

    expect(button().disabled).toBe(true);
  });

  it("says it is working while the write is in flight", () => {
    render(
      <TemplateRail comparison={DEMO_COMPARISON} onScheduleNext={vi.fn()} pending="agenda" />,
    );

    expect(screen.getByRole("button", { name: "처리 중" })).toBeTruthy();
  });
});
