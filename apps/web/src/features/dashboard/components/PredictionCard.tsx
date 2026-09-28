import { stepFor } from "./chartScale";
import { DashboardCard } from "./DashboardCard";
import { HoverPreview, PredictionMockup } from "./HoverPreview";
import type { PredictionsRead } from "../types";

/**
 * The misalignment-risk prediction on S26 — probability in mono, per ui-spec.
 *
 * What it predicts: the chance a decision from the team's latest meeting is
 * reversed within `horizon_days`. Whether to show it at all is the server's
 * call (#27: four weeks and three meetings); this renders whatever
 * `/predictions` returns and never re-derives the gate.
 */
export function PredictionCard({ predictions }: { predictions: PredictionsRead | null }) {
  const prediction = predictions?.prediction ?? null;

  if (!prediction) {
    const message =
      predictions?.reason === "insufficient_history"
        ? "4주 이상, 회의 3회 이상 쌓이면 표시됩니다 (#27)."
        : "아직 예측이 없습니다.";
    return (
      <HoverPreview mockup={<PredictionMockup />} side="left">
        <DashboardCard title="예측">
          <p style={metaStyle}>{message}</p>
        </DashboardCard>
      </HoverPreview>
    );
  }

  const percent = Math.round(prediction.probability * 100);
  const heuristic = prediction.model_version?.startsWith("heuristic") ?? false;

  return (
    <DashboardCard title="예측">
      <div
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          color: "var(--color-ink-strong)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {percent}%
      </div>
      <p style={{ ...metaStyle, marginTop: "var(--space-4)" }}>
        결정 번복 위험 · {prediction.horizon_days}일 이내
      </p>
      <div
        role="img"
        aria-label={`결정 번복 위험 ${percent}%`}
        style={{
          marginTop: "var(--space-8)",
          height: "var(--bar-thickness)",
          borderRadius: 3,
          background: "var(--color-surface-sunken)",
          overflow: "hidden",
        }}
      >
        <div
          style={{ height: "100%", width: `${percent}%`, background: stepFor(prediction.probability) }}
        />
      </div>
      {heuristic ? (
        <p style={{ ...metaStyle, marginTop: "var(--space-8)", fontSize: "var(--text-metaSmall)" }}>
          학습 전 기준선 추정치입니다. 회의 기록이 쌓이면 학습된 모델로 바뀝니다.
        </p>
      ) : null}
    </DashboardCard>
  );
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
