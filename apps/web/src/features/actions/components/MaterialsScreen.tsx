"use client";

import { useEffect, useState } from "react";

import { getSession, type SessionUser } from "@/shared/api/auth";
import { ApiError } from "@/shared/api/client";
import { driveOpenUrl, parseDriveLink } from "@/shared/drive/driveLink";
import { DrivePreview } from "@/shared/drive/DrivePreview";
import { Button } from "@/shared/ui";

import { deleteMaterial, listMaterials, registerMaterial } from "../api";
import { localToday } from "../dates";
import type { Material } from "../types";

type Team = SessionUser["teams"][number];

const KIND: Record<Material["drive_kind"], string> = {
  file: "Drive 파일",
  document: "Google 문서",
  presentation: "Google 프레젠테이션",
  spreadsheets: "Google 스프레드시트",
};

/**
 * The sidebar's "자료": the Google Drive files a team keeps, registered by a
 * pasted link and a typed title (#817; the user, 2026-10-08).
 *
 * **A link and a title, and nothing of the file.** Autune keeps which file it
 * is and reads no byte of it; a row opens in Google's own preview
 * (`DrivePreview`, #844) under the viewer's own Google sign-in, so the list
 * shows a file's title to the team and the file itself only to those Google
 * lets see it. The screen says so, because a shelf of documents reads as
 * "Autune has our documents" unless told otherwise.
 *
 * Any member registers and deletes, as with the team's projects: there is no
 * admin role yet (#592). The team is the one chosen elsewhere in the app when
 * the route passes it -- the same two props as `IntegrationSettingsScreen`,
 * for the same reason.
 */
export function MaterialsScreen({
  chosenTeamId = null,
  onChooseTeam,
}: {
  /** The team chosen elsewhere in the app, when the route knows one. */
  chosenTeamId?: string | null;
  /** Told when a team is picked here, so the rest of the app can follow. */
  onChooseTeam?: (teamId: string) => void;
} = {}) {
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const mine = teams ?? [];
  const known = (id: string | null) =>
    id !== null && mine.some((t) => t.id === id) ? id : null;
  const own = onChooseTeam === undefined ? known(picked) : null;
  const teamId = own ?? known(chosenTeamId) ?? mine[0]?.id ?? null;
  const setTeamId = onChooseTeam ?? setPicked;

  useEffect(() => {
    let alive = true;
    void getSession().then((user) => {
      if (!alive) return;
      setTeams(user?.teams ?? []);
    });
    return () => {
      alive = false;
    };
  }, []);

  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  return (
    <main
      className="flex max-w-[860px] flex-col gap-6"
      style={{ padding: "20px var(--space-page) var(--space-page)" }}
    >
      <h1
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          letterSpacing: "var(--text-title-tracking)",
        }}
      >
        자료
      </h1>
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        팀이 함께 보는 Google Drive 파일의 제목과 링크를 모아 둡니다. Autune은
        파일의 내용을 읽거나 저장하지 않습니다. 미리보기는 보는 사람 본인의
        Google 로그인으로 Google이 보여 주므로, 파일을 볼 권한이 없는 사람에게는
        보이지 않습니다. 팀 구성원이면 누구나 등록하고 목록에서 뺄 수 있습니다.
      </p>

      {teams === null ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          팀을 불러오는 중입니다.
        </p>
      ) : teams.length === 0 || teamId === null ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          속한 팀이 없습니다. 팀에 들어가면 그 팀의 자료를 등록하고 볼 수
          있습니다.
        </p>
      ) : (
        <>
          {teams.length > 1 ? (
            <label className="flex items-center gap-2" style={meta}>
              팀
              <select
                value={teamId}
                onChange={(event) => setTeamId(event.target.value)}
              >
                {teams.map((team) => (
                  <option key={team.id} value={team.id}>
                    {team.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <TeamMaterials key={teamId} teamId={teamId} />
        </>
      )}
    </main>
  );
}

/** One team's shelf: the form that adds to it, and the list. */
function TeamMaterials({ teamId }: { teamId: string }) {
  const [materials, setMaterials] = useState<Material[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listMaterials(teamId)
      .then((list) => alive && setMaterials(list))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [teamId]);

  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  if (failed) {
    return (
      <p role="alert" className="text-[var(--color-signal-critical)]" style={meta}>
        자료를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.
      </p>
    );
  }
  if (materials === null) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        자료를 불러오는 중입니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <RegisterForm
        onRegister={async (draft) => {
          const saved = await registerMaterial(teamId, draft);
          setMaterials((list) => [saved, ...(list ?? [])]);
          setNote("자료를 등록했습니다.");
        }}
      />
      {note ? (
        <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
          {note}
        </span>
      ) : null}
      {materials.length === 0 ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          아직 등록한 자료가 없습니다. Drive에서 &lsquo;링크 복사&rsquo;로 받은
          주소를 위에 넣어 등록해 주세요.
        </p>
      ) : (
        <ul className="flex flex-col" aria-label="등록한 자료">
          {materials.map((material) => (
            <MaterialRow
              key={material.id}
              material={material}
              onDelete={async () => {
                try {
                  await deleteMaterial(teamId, material.id);
                } catch {
                  setNote("목록에서 빼지 못했습니다. 잠시 후 다시 시도해 주세요.");
                  return;
                }
                setMaterials((list) =>
                  (list ?? []).filter((m) => m.id !== material.id),
                );
                setNote("목록에서 뺐습니다. Drive의 파일은 그대로 있습니다.");
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

/** A title and a pasted Drive link. The link is checked here before it is sent. */
function RegisterForm({
  onRegister,
}: {
  onRegister: (draft: { title: string; link: string }) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = {
    fontSize: "var(--text-metaSmall)",
    borderRadius: "var(--radius)",
    padding: "4px 8px",
  } as const;

  const submit = async () => {
    // The same parser the preview uses: a link it cannot read would register
    // a row that can never be opened, and the server refuses it by the same
    // rules -- this only says so sooner and in the person's words.
    if (parseDriveLink(link) === null) {
      setError(
        "Google Drive 파일의 링크가 아닙니다. Drive에서 ‘링크 복사’로 받은 주소를 넣어 주세요. 폴더 링크는 등록할 수 없습니다.",
      );
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await onRegister({ title: title.trim(), link: link.trim() });
      setTitle("");
      setLink("");
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.status === 409
          ? "이 팀에 이미 등록된 파일입니다."
          : cause instanceof ApiError && cause.status === 422
            ? "등록하지 못했습니다. 제목과 링크를 확인해 주세요."
            : "등록하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      aria-label="자료 등록"
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="자료 제목"
          placeholder="제목"
          value={title}
          maxLength={120}
          onChange={(event) => setTitle(event.target.value)}
          className="w-56 border border-[var(--color-hairline)]"
          style={input}
        />
        <input
          aria-label="Drive 링크"
          placeholder="https://drive.google.com/file/d/…"
          value={link}
          maxLength={2000}
          onChange={(event) => setLink(event.target.value)}
          className="min-w-0 flex-1 border border-[var(--color-hairline)]"
          style={input}
        />
        <Button
          type="submit"
          tone="secondary"
          size="compact"
          loading={busy}
          disabled={!title.trim() || !link.trim()}
        >
          자료 등록
        </Button>
      </div>
      {error ? (
        <span
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {error}
        </span>
      ) : null}
    </form>
  );
}

/** One material: its title, what kind of file, and the three things done with it. */
function MaterialRow({
  material,
  onDelete,
}: {
  material: Material;
  onDelete: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  // Google's address, built from the id the server kept -- never from text a
  // person pasted (`driveLink.ts`).
  const address = driveOpenUrl({
    id: material.drive_file_id,
    kind: material.drive_kind,
  });
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  return (
    <li
      className="flex flex-col gap-2 border-b border-[var(--color-hairline)] py-3"
      style={{ listStyle: "none" }}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className="min-w-0 flex-1 break-words text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-body)" }}
        >
          {material.title}
        </span>
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          {KIND[material.drive_kind]} · {localToday(new Date(material.created_at))} 등록
        </span>
        <Button
          tone="quiet"
          size="compact"
          aria-expanded={open}
          onClick={() => setOpen((shown) => !shown)}
        >
          {open ? "미리보기 닫기" : "미리보기"}
        </Button>
        <a
          className="text-[var(--color-accent-default)]"
          style={meta}
          href={address}
          target="_blank"
          rel="noopener noreferrer"
        >
          Drive에서 열기
        </a>
        {asking ? null : (
          <Button tone="destructiveText" size="compact" onClick={() => setAsking(true)}>
            삭제
          </Button>
        )}
      </div>
      {asking ? (
        <div
          role="group"
          aria-label="자료 삭제 확인"
          className="flex flex-wrap items-center gap-2"
          style={meta}
        >
          <span className="text-[var(--color-ink-body)]">
            이 자료를 팀의 목록에서 뺄까요? Drive의 파일은 그대로 남습니다.
          </span>
          <Button tone="quiet" size="compact" disabled={busy} onClick={() => setAsking(false)}>
            취소
          </Button>
          {/* Red text asks, an accent fill confirms; red never fills a button
              (ui-spec section 0). */}
          <Button
            tone="primary"
            size="compact"
            loading={busy}
            onClick={() => {
              setBusy(true);
              void onDelete().finally(() => {
                setBusy(false);
                setAsking(false);
              });
            }}
          >
            목록에서 빼기
          </Button>
        </div>
      ) : null}
      {open ? <DrivePreview link={address} title={material.title} /> : null}
    </li>
  );
}
