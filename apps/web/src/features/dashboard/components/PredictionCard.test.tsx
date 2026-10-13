import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PredictionCard } from "./PredictionCard";
import type { PredictionsRead } from "../types";

// The card shows the stored risk as it is, under a title that does not claim
// a forecast: the heuristic baseline is unvalidated.

afterEach(cleanup);

function predictions(probability: number, modelVersion = "heuristic-v1"): PredictionsRead {
  return {
    team_id: "team_1",
    reason: null,
    prediction: {
      meeting_id: "meeting_1",
      kind: "misalignment_risk",
      horizon_days: 14,
      probability,
      model_version: modelVersion,
      updated_at: "2026-10-05T06:20:00Z",
    },
  };
}

describe("PredictionCard", () => {
  it("shows the stored risk as the likelihood a decision is revisited", () => {
    render(<PredictionCard predictions={predictions(0.62)} />);

    expect(screen.getByText("결정 재검토 가능성")).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();
    expect(screen.getByText("14일 안에 재검토될 확률")).toBeTruthy();
    expect(screen.getByRole("img", { name: "결정 재검토 가능성 62%" })).toBeTruthy();
    expect(screen.queryByText(/예측|번복/)).toBeNull();
  });

  it("keeps the baseline note for a heuristic and drops it for a trained model", () => {
    render(<PredictionCard predictions={predictions(0.2)} />);
    expect(screen.getByText(/학습 전 기준선 추정치입니다/)).toBeTruthy();

    cleanup();
    render(<PredictionCard predictions={predictions(0.2, "xgboost-v1")} />);
    expect(screen.queryByText(/학습 전 기준선 추정치입니다/)).toBeNull();
  });

  it("says nothing is computed yet without calling it a prediction", () => {
    render(
      <PredictionCard predictions={{ team_id: "team_1", prediction: null, reason: "no_prediction" }} />,
    );

    expect(screen.getByText("아직 결정 재검토 가능성을 계산하지 않았습니다.")).toBeTruthy();
  });
});
