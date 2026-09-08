import { Button } from "@/shared/ui/Button";
import { Row } from "@/shared/ui/Row";
import { StatusDot } from "@/shared/ui/StatusDot";

/**
 * Placeholder home. Screen S05 is built in W4 by the audio owner.
 * This page exists so the shell renders and the token wiring is visible.
 */
export default function Home() {
  return (
    <main className="mx-auto max-w-[720px] p-[var(--space-page)]">
      <h1
        className="text-ink-strong"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          letterSpacing: "var(--text-title-tracking)",
        }}
      >
        Autune
      </h1>
      <p className="mt-2 text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        프론트엔드 셸이 준비되었습니다. 화면은 docs/design/ui-spec.md 의 S01–S33 을 따릅니다.
      </p>

      <div className="mt-6 border-t border-hairline">
        <Row
          dot={<StatusDot variant="progress" />}
          title="검색 개인화 기능 스프린트 킥오프"
          meta="분석 중 62%"
          actions={<Button tone="text" size="compact">열기</Button>}
        />
        <Row
          dot={<StatusDot variant="attention" />}
          title="디자인 시스템 정리 회의"
          meta="화자 1명 미확인 · 발화 6건"
          actions={<Button tone="text" size="compact">확인하기</Button>}
        />
        <Row
          dot={<StatusDot variant="confirmed" />}
          title="검색 개편 우선순위 논의"
          meta="액션 5 · 갭 2 · 결정 3"
        />
      </div>
    </main>
  );
}
