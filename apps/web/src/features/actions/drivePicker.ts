/**
 * Choosing a file from Google Drive for the 자료 upload (#1369).
 *
 * **The browser fetches the file; the server never touches Drive.** Google's
 * Picker runs in this page, the person picks one file under their own Google
 * sign-in, the page downloads that file with a short-lived token, and the
 * bytes go through the upload route a file chosen from disk goes through. The
 * server sees a file and a title, exactly as it does now: it reads, masks and
 * stores, and the original is not kept (invariant 11, `privacy.md`).
 *
 * - **One scope, `drive.file`.** It reaches only the files the person picks
 *   here (non-sensitive in Google's scope list). No file id is sent to Autune.
 * - **The token is never stored.** It lives in the closure of one call; not in
 *   storage, not in state, not in a request to our API.
 * - **Google's scripts load on a click, not with the page** (#1369, answer 1).
 *   `loadScript` is called from `pickDriveFile` alone, so no screen brings a
 *   Google script in by itself. The 자료 screen's button is the only trigger,
 *   but a script once loaded stays until the page is reloaded: a client-side
 *   move to another screen does not remove it (mminjae97 on #1369).
 * - **Off unless configured.** `drivePickerConfig` is null without the three
 *   build-time values, and the screen then draws no button.
 *
 * The messages of `DrivePickError` say what failed and never the file's name
 * or content.
 */

/** Google's loaders. Only `pickDriveFile` brings them in. */
const GAPI_SRC = "https://apis.google.com/js/api.js";
const GIS_SRC = "https://accounts.google.com/gsi/client";

/** The one scope asked for: only files the person picks in the Picker. */
export const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";

const DRIVE_API = "https://www.googleapis.com/drive/v3/files";

export interface DrivePickerConfig {
  clientId: string;
  apiKey: string;
  /** The Google Cloud project number the client and the key belong to. */
  appId: string;
}

/**
 * The three build-time values, or null when any is missing. The same Google
 * Cloud project must hold all three (Picker's `setAppId`), so a partial set is
 * as good as none.
 */
export function drivePickerConfig(): DrivePickerConfig | null {
  // Each is spelled out in full: Next inlines `process.env.NEXT_PUBLIC_X`
  // only where the whole name appears.
  const clientId = (process.env.NEXT_PUBLIC_GOOGLE_PICKER_CLIENT_ID ?? "").trim();
  const apiKey = (process.env.NEXT_PUBLIC_GOOGLE_PICKER_API_KEY ?? "").trim();
  const appId = (process.env.NEXT_PUBLIC_GOOGLE_PICKER_APP_ID ?? "").trim();
  return clientId && apiKey && appId ? { clientId, apiKey, appId } : null;
}

/** A Drive file the Picker returned: what the download needs, no more. */
export interface PickedDoc {
  id: string;
  name: string;
  mimeType: string;
}

const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/**
 * Google's own editors have no bytes to download; they are exported into the
 * Office format the server's readers (`material_reading`) take.
 */
const EXPORTS: Record<string, { mime: string; suffix: string }> = {
  "application/vnd.google-apps.document": { mime: DOCX, suffix: ".docx" },
  "application/vnd.google-apps.presentation": { mime: PPTX, suffix: ".pptx" },
  "application/vnd.google-apps.spreadsheet": { mime: XLSX, suffix: ".xlsx" },
};

/** What the Picker offers: the formats the upload route reads, and the three editors. */
const PICKER_MIME_TYPES = [
  "application/pdf",
  DOCX,
  PPTX,
  XLSX,
  "text/plain",
  "text/csv",
  ...Object.keys(EXPORTS),
];

/** A failure the person can be told, in the words to show. */
export class DrivePickError extends Error {}

/**
 * Where to fetch a picked file and what to call it, or a `DrivePickError`
 * for a Google file that has no readable export (a form, a drawing, a folder).
 */
export function downloadPlan(doc: PickedDoc): { url: string; fileName: string } {
  const exported = EXPORTS[doc.mimeType];
  if (exported) {
    const name = doc.name.toLowerCase().endsWith(exported.suffix)
      ? doc.name
      : `${doc.name}${exported.suffix}`;
    return {
      url: `${DRIVE_API}/${encodeURIComponent(doc.id)}/export?mimeType=${encodeURIComponent(exported.mime)}`,
      fileName: name,
    };
  }
  if (doc.mimeType.startsWith("application/vnd.google-apps.")) {
    throw new DrivePickError("이 종류의 Google 파일은 올릴 수 없습니다. 문서, 프레젠테이션, 스프레드시트만 받습니다.");
  }
  return {
    url: `${DRIVE_API}/${encodeURIComponent(doc.id)}?alt=media`,
    fileName: doc.name,
  };
}

/**
 * Download one picked file with the token. A size the upload rules would
 * refuse is stopped from the header, before the body is read.
 */
export async function downloadPicked(
  doc: PickedDoc,
  token: string,
  maxBytes: number,
  fetcher: typeof fetch = fetch,
): Promise<File> {
  const { url, fileName } = downloadPlan(doc);
  let response: Response;
  try {
    response = await fetcher(url, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw new DrivePickError("Drive에서 파일을 받지 못했습니다. 연결을 확인하고 다시 시도해 주세요.");
  }
  if (!response.ok) {
    throw new DrivePickError(
      response.status === 403 || response.status === 404
        ? "이 파일을 열 수 없습니다. Autune이 접근할 수 있는 것은 이번에 고른 파일뿐입니다. 다시 골라 주세요."
        : `Drive에서 파일을 받지 못했습니다 (${response.status}).`,
    );
  }
  const length = Number(response.headers.get("content-length") ?? "");
  if (Number.isFinite(length) && length > maxBytes) {
    throw new DrivePickError("파일이 너무 큽니다.");
  }
  const blob = await response.blob();
  return new File([blob], fileName, { type: blob.type });
}

// ---- Google's page-level objects, as far as this file uses them ----

interface PickerDoc {
  id: string;
  name: string;
  mimeType: string;
}
interface PickerResult {
  action: string;
  docs?: PickerDoc[];
}
interface GoogleWindow {
  gapi?: { load: (name: string, callback: () => void) => void };
  google?: {
    accounts: {
      oauth2: {
        initTokenClient: (config: {
          client_id: string;
          scope: string;
          callback: (response: { access_token?: string; error?: string }) => void;
          error_callback?: (error: { type?: string }) => void;
        }) => { requestAccessToken: (options?: { prompt?: string }) => void };
      };
    };
    picker: {
      Action: { PICKED: string; CANCEL: string };
      ViewId: { DOCS: string };
      DocsView: new (viewId: string) => {
        setMimeTypes: (types: string) => unknown;
        setIncludeFolders: (on: boolean) => unknown;
        setSelectFolderEnabled: (on: boolean) => unknown;
      };
      PickerBuilder: new () => {
        addView: (view: unknown) => unknown;
        setOAuthToken: (token: string) => unknown;
        setDeveloperKey: (key: string) => unknown;
        setAppId: (id: string) => unknown;
        setCallback: (callback: (result: PickerResult) => void) => unknown;
        build: () => { setVisible: (on: boolean) => void };
      };
    };
  };
}

const google = (): GoogleWindow => window as unknown as GoogleWindow;

const scripts = new Map<string, Promise<void>>();

/** Add one script tag, once; a failed load may be tried again. */
function loadScript(src: string): Promise<void> {
  const known = scripts.get(src);
  if (known) return known;
  const loading = new Promise<void>((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = src;
    tag.async = true;
    tag.onload = () => resolve();
    tag.onerror = () => {
      scripts.delete(src);
      tag.remove();
      reject(new DrivePickError("Google의 파일 선택 창을 불러오지 못했습니다. 잠시 뒤 다시 시도해 주세요."));
    };
    document.head.appendChild(tag);
  });
  scripts.set(src, loading);
  return loading;
}

function requestToken(clientId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const refused = () =>
      reject(new DrivePickError("Google 로그인 또는 파일 접근을 허용하지 않아 파일을 고를 수 없습니다."));
    const client = google().google?.accounts.oauth2.initTokenClient({
      client_id: clientId,
      scope: DRIVE_SCOPE,
      callback: (response) => (response.access_token ? resolve(response.access_token) : refused()),
      error_callback: refused,
    });
    if (!client) return refused();
    client.requestAccessToken();
  });
}

/** The file the person picks, or null when they close the Picker. */
function pick(config: DrivePickerConfig, token: string): Promise<PickedDoc | null> {
  return new Promise((resolve, reject) => {
    const picker = google().google?.picker;
    if (!picker) {
      reject(new DrivePickError("Google의 파일 선택 창을 불러오지 못했습니다."));
      return;
    }
    const view = new picker.DocsView(picker.ViewId.DOCS);
    view.setMimeTypes(PICKER_MIME_TYPES.join(","));
    view.setIncludeFolders(true);
    view.setSelectFolderEnabled(false);
    const builder = new picker.PickerBuilder();
    builder.addView(view);
    builder.setOAuthToken(token);
    builder.setDeveloperKey(config.apiKey);
    builder.setAppId(config.appId);
    builder.setCallback((result) => {
      if (result.action === picker.Action.PICKED && result.docs?.[0]) {
        const { id, name, mimeType } = result.docs[0];
        resolve({ id, name, mimeType });
      } else if (result.action === picker.Action.CANCEL) {
        resolve(null);
      }
    });
    builder.build().setVisible(true);
  });
}

/**
 * Open Google's Picker and return the chosen file as a `File`, or null when
 * the person closes it. Loads Google's scripts on first use.
 */
export async function pickDriveFile(
  config: DrivePickerConfig,
  maxBytes: number,
): Promise<File | null> {
  await Promise.all([loadScript(GAPI_SRC), loadScript(GIS_SRC)]);
  await new Promise<void>((resolve, reject) => {
    const gapi = google().gapi;
    if (!gapi) {
      reject(new DrivePickError("Google의 파일 선택 창을 불러오지 못했습니다."));
      return;
    }
    gapi.load("picker", () => resolve());
  });
  const token = await requestToken(config.clientId);
  const doc = await pick(config, token);
  if (doc === null) return null;
  return downloadPicked(doc, token, maxBytes);
}

/** The words to show for whatever went wrong while choosing. */
export function driveRefusal(cause: unknown): string {
  return cause instanceof DrivePickError
    ? cause.message
    : "Drive에서 파일을 고르지 못했습니다. 다시 시도해 주세요.";
}
