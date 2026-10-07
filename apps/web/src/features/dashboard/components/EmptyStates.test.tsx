import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AlignmentHeatmap } from "./AlignmentHeatmap";
import { PredictionCard } from "./PredictionCard";

// The empty states explain the server's gates to a person, so they name every
// condition and no GitHub issue number.

afterEach(cleanup);

describe("dashboard empty states", () => {
  it("says the prediction needs four weeks and three meetings, both", () => {
    render(
      <PredictionCard
        predictions={{ team_id: "team_1", prediction: null, reason: "insufficient_history" }}
      />,
    );

    const message = screen.getByText(/4주가 지나고, 분석한 회의가 3회 이상이면 표시됩니다\.$/);
    expect(message.textContent).not.toMatch(/#\d/);
  });

  it("says the heatmap needs three people in a role and three meetings per pair", () => {
    render(<AlignmentHeatmap cells={[]} />);

    const message = screen.getByText(/^표본이 충분한 직무 쌍이 아직 없습니다\./);
    expect(message.textContent).toMatch(/3명 이상 참석한 회의/);
    expect(message.textContent).toMatch(/회의 3회 이상/);
    expect(message.textContent).not.toMatch(/#\d/);
  });
});
