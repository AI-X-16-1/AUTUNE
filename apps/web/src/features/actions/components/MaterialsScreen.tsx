"use client";

import { useEffect, useState } from "react";

import { getSession, type SessionUser } from "@/shared/api/auth";
import { ApiError } from "@/shared/api/client";
import { driveOpenUrl, parseDriveLink } from "@/shared/drive/driveLink";
import { DrivePreview } from "@/shared/drive/DrivePreview";
import { Button } from "@/shared/ui";

import {
  deleteMaterial,
  getMaterialUploadRules,
  listMaterials,
  registerMaterial,
  uploadMaterial,
} from "../api";
import { localToday } from "../dates";
import { deletionDay, notReadNote, shelfFull } from "../materialUpload";
import { typedTextRefusal } from "../refusal";
import type { Material, MaterialUploadRules } from "../types";
import { MaterialUploadForm } from "./MaterialUploadForm";

type Team = SessionUser["teams"][number];

const KIND: Record<NonNullable<Material["drive_kind"]>, string> = {
  file: "Drive 파일",
  document: "Google 문서",
  presentation: "Google 프레젠테이션",
  spreadsheets: "Google 스프레드시트",
};

/**
 * The sidebar's "자료": what a team keeps to read beside its meetings
 * (#817; the user, 2026-10-08). Two kinds of row, and the screen keeps them
 * apart in what it says, because what Autune holds of each is different.
 *
 * **A Drive link: a title, and nothing of the file.** Autune keeps which file
 * it is and reads no byte of it; a row opens in Google's own preview
 * (`DrivePreview`, #844) under the viewer's own Google sign-in, so the list
 * shows a file's title to the team and the file itself only to those Google
 * lets see it. The screen says so, because a shelf of documents reads as
 * "Autune has our documents" unless told otherwise.
 *
 * **An uploaded file: its masked text, until a day, and no original.** Only
 * where the deployment takes uploads (`MaterialUploadRules.enabled`); off,
 * the screen is the link shelf it was. Such a row has nothing to preview and
 * nothing to open -- Autune kept no file -- and it shows the day its text is
 * deleted. Deleting it is real and at once, so the question before it says
 * that and not "take it off the list".
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
        팀이 함께 보는 자료를 모아 둡니다. 링크로 등록한 Google Drive 파일은
        제목과 링크만 두며, Autune은 그 파일의 내용을 읽거나 저장하지 않습니다.
        미리보기는 보는 사람 본인의 Google 로그인으로 Google이 보여 주므로, 파일을
        볼 권한이 없는 사람에게는 보이지 않습니다. 팀 구성원이면 누구나 등록하고
        목록에서 뺄 수 있습니다.
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
  // Null until the server says, and null when it cannot: a server without the
  // route is one that takes no uploads, and the screen is the link shelf.
  const [rules, setRules] = useState<MaterialUploadRules | null>(null);

  useEffect(() => {
    let alive = true;
    listMaterials(teamId)
      .then((list) => alive && setMaterials(list))
      .catch(() => alive && setFailed(true));
    getMaterialUploadRules(teamId)
      .then((answer) => alive && setRules(answer))
      .catch(() => alive && setRules(null));
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
        rules={rules}
        onRegister={async (draft) => {
          // The note is about the last thing done; this is a new one.
          setNote(null);
          const saved = await registerMaterial(teamId, draft);
          setMaterials((list) => [saved, ...(list ?? [])]);
          setNote("자료를 등록했습니다.");
        }}
      />
      {rules?.enabled ? (
        <MaterialUploadForm
          rules={rules}
          onUpload={async (draft) => {
            // "자료를 올렸습니다" under a refusal would read as this file's.
            setNote(null);
            const saved = await uploadMaterial(teamId, draft);
            setMaterials((list) => [saved, ...(list ?? [])]);
            const day = saved.expires_at ? localToday(new Date(saved.expires_at)) : null;
            setNote(
              [
                day
                  ? `자료를 올렸습니다. 개인정보를 가린 글을 ${day}까지 보관합니다.`
                  : "자료를 올렸습니다.",
                notReadNote(saved.not_read ?? []),
              ]
                .filter(Boolean)
                .join(" "),
            );
          }}
          onUnanswered={() => {
            // The answer was lost, not necessarily the row: the list says.
            void listMaterials(teamId)
              .then(setMaterials)
              .catch(() => undefined);
          }}
        />
      ) : null}
      {note ? (
        <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
          {note}
        </span>
      ) : null}
      {materials.length === 0 ? (
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          아직 등록한 자료가 없습니다. Drive에서 &lsquo;링크 복사&rsquo;로 받은
          주소를 위에 넣어 등록해 주세요.
          {rules?.enabled ? " 파일을 올려 둘 수도 있습니다." : null}
        </p>
      ) : (
        <ul className="flex flex-col" aria-label="등록한 자료">
          {materials.map((material) => (
            <MaterialRow
              key={material.id}
              material={material}
              onDelete={async () => {
                const uploaded = material.source === "upload";
                let gone = false;
                try {
                  await deleteMaterial(teamId, material.id);
                } catch (cause) {
                  // Somebody else deleted it, or its day came: it is not
                  // there, which is what was asked for.
                  gone = cause instanceof ApiError && cause.status === 404;
                  if (!gone) {
                    setNote(
                      uploaded
                        ? "삭제하지 못했습니다. 잠시 후 다시 시도해 주세요."
                        : "목록에서 빼지 못했습니다. 잠시 후 다시 시도해 주세요.",
                    );
                    return;
                  }
                }
                setMaterials((list) =>
                  (list ?? []).filter((m) => m.id !== material.id),
                );
                setNote(
                  gone
                    ? "이미 삭제된 자료입니다."
                    : uploaded
                      ? "자료를 삭제했습니다. 보관하던 글도 함께 지웠습니다."
                      : "목록에서 뺐습니다. Drive의 파일은 그대로 있습니다.",
                );
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
  rules,
  onRegister,
}: {
  /** The server's numbers, where it gave them: a full shelf is said with its limit. */
  rules: MaterialUploadRules | null;
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
            ? (typedTextRefusal(cause) ??
              shelfFull(cause, rules) ??
              "등록하지 못했습니다. 제목과 링크를 확인해 주세요.")
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

/**
 * One material: its title, what kind of row it is, and what can be done with
 * it. A link opens in Google's preview and in Drive; an upload shows the day
 * its text goes and can only be deleted.
 */
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
  // A row is an upload only when the server says so: one from a server that
  // knows links alone has no `source` and is a link.
  const uploaded = material.source === "upload";
  // Google's address, built from the id the server kept -- never from text a
  // person pasted (`driveLink.ts`). An upload has no file anywhere to open.
  const address =
    !uploaded && material.drive_file_id !== null && material.drive_kind !== null
      ? driveOpenUrl({ id: material.drive_file_id, kind: material.drive_kind })
      : null;
  const day = uploaded ? deletionDay(material.expires_at ?? null) : null;
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
          {uploaded
            ? "올린 파일"
            : material.drive_kind
              ? KIND[material.drive_kind]
              : "Drive 파일"}{" "}
          · {localToday(new Date(material.created_at))} 등록
          {day ? ` · ${day}` : null}
        </span>
        {address ? (
          <>
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
          </>
        ) : null}
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
            {uploaded
              ? "이 자료를 삭제할까요? 보관 중인 글이 바로 지워지고 되돌릴 수 없습니다. Autune에는 원본 파일이 없어 다시 올려야 합니다."
              : "이 자료를 팀의 목록에서 뺄까요? Drive의 파일은 그대로 남습니다."}
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
            {uploaded ? "삭제" : "목록에서 빼기"}
          </Button>
        </div>
      ) : null}
      {open && address ? <DrivePreview link={address} title={material.title} /> : null}
    </li>
  );
}
