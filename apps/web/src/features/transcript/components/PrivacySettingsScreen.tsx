"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { Button, ChipToggle, StatusDot } from "@/shared/ui";

import {
  deleteAccount,
  deleteMaskingRule,
  deleteMySpeech,
  deleteVoiceProfile,
  downloadMyData,
  getMyData,
  getTeamPrivacy,
  listMaskingRules,
  setTeamRetention,
} from "../api";
import type { MaskingRule, MyData, RetentionDays } from "../types";
import { TeamScope } from "./TeamScope";

/**
 * S29 — 설정 › 개인정보 · 보관 (#554). The workspace's policy, and my own data.
 *
 * **A row only has a control when the control does something.** The design
 * draws seven policy rows; the ones the product enforces in code and nobody
 * may switch off carry the "항상 켬" dot and no toggle (ui-spec section 0).
 * "추가 마스킹 항목" lists the shapes the team learned from S30 reports, each
 * removable. Rows whose setting does not exist yet — consent on the first
 * meeting only — say "준비 중" instead of offering a choice that
 * would be stored nowhere. The design's "팀 탈퇴" row is replaced by account
 * deletion: there is no way to leave a team yet, and the row would promise a
 * deletion nothing performs.
 *
 * Everything under "내 데이터" is the caller's own and is computed with
 * `user_id == caller` on the server. No speaking ratio appears: it goes to the
 * speaker by DM and is not stored (privacy.md section 3).
 */

const RETENTION: { days: RetentionDays; label: string }[] = [
  { days: 30, label: "30일" },
  { days: 90, label: "90일" },
  { days: 180, label: "180일" },
  { days: 365, label: "1년" },
];

const SECTION_TITLE = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-muted)",
} as const;
const META = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;
const ERROR = { fontSize: "var(--text-meta)", color: "var(--color-signal-critical)" } as const;

type Pending = "speech" | "voice" | "account";

export function PrivacySettingsScreen() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <div className="max-w-[760px]">
        <h1
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          개인정보 · 보관
        </h1>
        <p className="mt-1" style={META}>
          워크스페이스 정책과 내 데이터
        </p>

        <section className="mt-6" aria-labelledby="policy-title">
          <h2 id="policy-title" style={SECTION_TITLE}>
            워크스페이스 정책
          </h2>
          <div className="mt-2">
            <TeamScope>{(teamId) => <WorkspacePolicy teamId={teamId} />}</TeamScope>
          </div>
        </section>

        <section className="mt-8" aria-labelledby="mine-title">
          <h2 id="mine-title" style={SECTION_TITLE}>
            내 데이터
          </h2>
          <MyDataSection />
        </section>
      </div>
    </main>
  );
}

function PolicyRow({
  title,
  description,
  control,
}: {
  title: string;
  description: string;
  control: ReactNode;
}) {
  return (
    <div
      className="flex items-center gap-4 border-b border-[var(--color-hairline)]"
      style={{ paddingBlock: "var(--space-row)" }}
    >
      <div className="min-w-0 flex-1">
        <div
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
        >
          {title}
        </div>
        <div className="mt-0.5" style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-ink-muted)" }}>
          {description}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">{control}</div>
    </div>
  );
}

function AlwaysOn() {
  return (
    <span className="inline-flex items-center gap-1.5" style={{ fontSize: "var(--text-status)" }}>
      <StatusDot variant="confirmed" />
      <span className="text-[var(--color-ink-body)]">항상 켬</span>
    </span>
  );
}

function WorkspacePolicy({ teamId }: { teamId: string }) {
  const [retention, setRetention] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setRetention(null);
    setError(null);
    getTeamPrivacy(teamId)
      .then((body) => current && setRetention(body.retention_days))
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : "보관 기간을 불러오지 못했습니다.");
      });
    return () => {
      current = false;
    };
  }, [teamId]);

  const choose = async (days: RetentionDays) => {
    if (days === retention || saving) return;
    setSaving(true);
    setError(null);
    try {
      setRetention((await setTeamRetention(teamId, days)).retention_days);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "보관 기간을 바꾸지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PolicyRow
        title="원본 음성"
        description="처리 후 삭제 · 복원 불가 · 변경할 수 없는 정책"
        control={<AlwaysOn />}
      />
      <PolicyRow
        title="개인정보 자동 마스킹"
        description="전화 · 이메일 · 주민번호 · 계좌 · 카드 — 정규식 + NER 이중 탐지 · 마스킹 전 원문 미저장"
        control={<AlwaysOn />}
      />
      <PolicyRow
        title="추가 마스킹 항목"
        description="개인정보 신고에서 추가한 사번·ID 형태 · 이후 회의에 자동 적용 · 팀원 누구나 지울 수 있음"
        control={<TeamRules teamId={teamId} />}
      />
      <PolicyRow
        title="분석 결과 보관 기간"
        description="요약 · 액션 · 갭 · 전사 텍스트. 만료 시 자동 삭제 · 이후 열리는 회의부터 적용"
        control={
          retention === null && !error ? (
            <span style={META}>불러오는 중…</span>
          ) : (
            RETENTION.map(({ days, label }) => (
              <ChipToggle key={days} selected={retention === days} onClick={() => void choose(days)}>
                {label}
              </ChipToggle>
            ))
          )
        }
      />
      <PolicyRow
        title="개인 발언 비중"
        description="본인 DM으로만 전달 · 서버 미저장 · 관리자 포함 누구도 타인 비중 조회 불가"
        control={<AlwaysOn />}
      />
      <PolicyRow
        title="참석자 동의"
        description="매 회의 녹음 전에 팀원이 전원 동의를 확인 · 최초 1회 방식은 준비 중"
        control={<span className="text-[var(--color-ink-body)]" style={{ fontSize: "var(--text-status)" }}>매 회의</span>}
      />
      {error && (
        <p role="alert" className="mt-2" style={ERROR}>
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * The team's own masking shapes, each with a remove control. A shape is
 * character classes only (`A-#####`), so listing it shows nobody's value.
 * Removing one stops masking it in later transcripts; what it already masked
 * stays masked, because the original was never stored.
 */
function TeamRules({ teamId }: { teamId: string }) {
  const [rules, setRules] = useState<MaskingRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listMaskingRules(teamId)
      .then((list) => current && setRules(list))
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : "규칙을 불러오지 못했습니다.");
      });
    return () => {
      current = false;
    };
  }, [teamId]);

  const remove = async (ruleId: number) => {
    setError(null);
    try {
      setRules(await deleteMaskingRule(teamId, ruleId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "규칙을 지우지 못했습니다.");
    }
  };

  if (error) return <span style={ERROR}>{error}</span>;
  if (rules === null) return <span style={META}>불러오는 중…</span>;
  if (rules.length === 0) return <span style={META}>없음</span>;
  return (
    <span className="flex flex-wrap justify-end gap-1.5">
      {rules.map((rule) => (
        <span
          key={rule.id}
          className="inline-flex items-center rounded-[var(--radius)] bg-[var(--color-surface-sunken)]"
          style={{ height: "var(--control-h-compact)", paddingLeft: 10 }}
        >
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-data)" }}>{rule.shape}</span>
          <Button tone="quiet" size="compact" aria-label={`${rule.shape} 규칙 삭제`} onClick={() => void remove(rule.id)}>
            ×
          </Button>
        </span>
      ))}
    </span>
  );
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return `${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`;
}

function MyDataSection() {
  const router = useRouter();
  const [data, setData] = useState<MyData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(() => {
    getMyData()
      .then(setData)
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : "내 데이터를 불러오지 못했습니다."),
      );
  }, []);

  useEffect(load, [load]);

  const download = async () => {
    setDownloading(true);
    setError(null);
    try {
      await downloadMyData();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "내려받지 못했습니다.");
    } finally {
      setDownloading(false);
    }
  };

  const confirm = async () => {
    if (!confirming) return;
    setBusy(true);
    setError(null);
    try {
      if (confirming === "account") {
        await deleteAccount();
        router.replace("/login");
        return;
      }
      if (confirming === "speech") await deleteMySpeech();
      else await deleteVoiceProfile();
      setConfirming(null);
      load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "삭제하지 못했습니다.");
      setConfirming(null);
    } finally {
      setBusy(false);
    }
  };

  const meetings = data?.meetings_with_my_speech ?? 0;

  return (
    <div className="mt-2">
      {data === null && !error && <p style={META}>불러오는 중…</p>}
      {data && (
        <dl className="grid grid-cols-2 gap-x-6 sm:grid-cols-4" style={{ rowGap: "var(--space-16)" }}>
          <Fact label="내 발화가 포함된 회의" value={`${data.meetings_with_my_speech}`} />
          <Fact
            label="음성 임베딩"
            value={
              data.voice_profile_rows > 0 && data.voice_profile_since
                ? `등록됨 · ${formatDate(data.voice_profile_since)}`
                : "없음"
            }
          />
          <Fact label="내가 확인한 녹음 동의" value={`${data.consents_attested}건`} />
          <Fact label="발언 비중" value="DM으로만 · 저장 안 함" />
        </dl>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button tone="secondary" onClick={() => void download()} loading={downloading}>
          내 데이터 내려받기 (JSON)
        </Button>
        <Button
          tone="destructiveText"
          onClick={() => setConfirming("voice")}
          disabled={!data || data.voice_profile_rows === 0}
        >
          음성 임베딩 삭제
        </Button>
        <Button tone="destructiveText" onClick={() => setConfirming("speech")} disabled={!data}>
          내 발화 데이터 모두 삭제
        </Button>
        <Button tone="destructiveText" onClick={() => setConfirming("account")} disabled={!data}>
          계정 삭제
        </Button>
      </div>

      {error && (
        <p role="alert" className="mt-2" style={ERROR}>
          {error}
        </p>
      )}

      {confirming && (
        <ConfirmModal
          title={TITLES[confirming]}
          body={bodyFor(confirming, meetings)}
          pending={busy}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void confirm()}
        />
      )}
    </div>
  );
}

const TITLES: Record<Pending, string> = {
  voice: "음성 임베딩을 삭제할까요?",
  speech: "내 발화 데이터를 모두 삭제할까요?",
  account: "계정을 삭제할까요?",
};

function bodyFor(kind: Pending, meetings: number): string {
  if (kind === "voice")
    return "등록된 음성 임베딩이 모두 삭제되고, 다음 회의부터 화자 자동 인식 후보에 나오지 않습니다. 이 작업은 되돌릴 수 없습니다.";
  if (kind === "speech")
    return `${meetings}개 회의에서 내 발화 텍스트와 음성 임베딩이 삭제됩니다. 회의에서 정리된 액션·결정은 남습니다. 이 작업은 되돌릴 수 없습니다.`;
  return `계정과 함께 ${meetings}개 회의의 내 발화 텍스트, 음성 임베딩, 연동 계정이 삭제되고 로그아웃됩니다. 회의에서 정리된 액션·결정은 남고, 팀이 Notion·Jira로 보낸 항목은 팀의 기록으로 남습니다. 이 작업은 되돌릴 수 없습니다.`;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-ink-muted)" }}>{label}</dt>
      <dd
        className="mt-0.5 text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
      >
        {value}
      </dd>
    </div>
  );
}

/**
 * The destructive-action modal (ui-spec section 0): red text button → this →
 * an accent-filled "삭제". Module B has one of its own (`ConfirmDelete`); a
 * feature may not import another, so this is A's.
 */
function ConfirmModal({
  title,
  body,
  pending,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(22,25,31,.35)" }}
      onClick={pending ? undefined : onCancel}
    >
      <div
        className="w-full max-w-[420px]"
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
          padding: "var(--space-card)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <h2
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-title)", fontWeight: "var(--text-title-weight)" }}
        >
          {title}
        </h2>
        <p
          className="mt-2 text-[var(--color-ink-body)]"
          style={{ fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" }}
        >
          {body}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button tone="quiet" onClick={onCancel} disabled={pending}>
            취소
          </Button>
          <Button tone="primary" onClick={onConfirm} loading={pending}>
            삭제
          </Button>
        </div>
      </div>
    </div>
  );
}
