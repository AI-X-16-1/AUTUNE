import { DashboardCard } from "./DashboardCard";
import { HoverPreview, PredictionMockup } from "./HoverPreview";
import type { PredictionsRead } from "../types";

/**
 * The server's gate as it stands: three analysed meetings. #27's four weeks of
 * history are lifted until the final presentation; restore the copy with them
 * ("처음 회의를 분석한 날부터 4주가 지나고, 분석한 회의가 3회 이상이면 표시됩니다.").
 */
const INSUFFICIENT_HISTORY = "분석한 회의가 3회 이상이면 표시됩니다.";

/**
 * The misalignment-risk prediction on S26 — probability in mono, per ui-spec.
 *
 * What it predicts: the chance a decision from the team's latest meeting is
 * reversed within `horizon_days`. Whether to show it at all is the server's
 * call (#27: three meetings; four weeks once restored); this renders whatever
 * `/predictions` returns and never re-derives the gate.
 */
export function PredictionCard({ predictions }: { predictions: PredictionsRead | null }) {
  const prediction = predictions?.prediction ?? null;

  if (!prediction) {
    const message =
      predictions?.reason === "insufficient_history"
        ? INSUFFICIENT_HISTORY
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
        {/*
          A fixed fill, like ActionCompletionRate and GapDistributionBars: the
          length carries the value and the colour carries nothing. Ramping the
          fill by value (stepFor) made this bar the only one on S26 that did,
          and it broke twice over. It vanished at low risk -- 33% lands on
          step2, which is 1.34:1 against this track -- and it read backwards
          against its neighbours, where a darker bar means a *better* number
          while here it would mean a worse one.
        */}
        <div
          style={{
            height: "100%",
            width: `${percent}%`,
            background: "var(--color-chart-step4)",
          }}
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
