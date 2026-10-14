// Builds the landing page into dist/, the folder Cloudflare Pages serves.
//
//   node apps/landing/build.mjs
//
// Two things happen beyond a copy of src/:
// - Every "%APP_ORIGIN%" in an HTML file becomes the app's address, so moving
//   the app to another domain is one value, not a hunt through the markup.
//   LANDING_APP_ORIGIN overrides it for a one-off build.
// - Pretendard, the face the app uses, is copied from the copy apps/web already
//   installs (run `pnpm install` first). The dynamic-subset build: the browser
//   fetches only the unicode ranges a page shows, and the fonts are served from
//   this site, so no font CDN learns who opened the page.
//
// No dependencies; Node 22, as the rest of the repo.

import { cpSync, existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Where the app is served today. The landing page lives on a different site
// from the app on purpose (#1320): an ad script on this page must never share
// an origin, or a site, with the app's session.
const DEFAULT_APP_ORIGIN = "https://autune.shelldocs.cloud";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "src");
const dist = join(here, "dist");
const appOrigin = (process.env.LANDING_APP_ORIGIN || DEFAULT_APP_ORIGIN).replace(/\/+$/, "");

if (!/^https:\/\/[a-z0-9.-]+$/i.test(appOrigin)) {
  throw new Error(`LANDING_APP_ORIGIN must be an https origin with no path, got "${appOrigin}"`);
}

const pretendard = join(here, "..", "web", "node_modules", "pretendard", "dist");
if (!existsSync(pretendard)) {
  throw new Error(`Pretendard not found at ${pretendard}; run \`pnpm install\` at the repo root first`);
}

rmSync(dist, { recursive: true, force: true });
cpSync(src, dist, { recursive: true });

for (const name of readdirSync(dist)) {
  if (!name.endsWith(".html")) continue;
  const path = join(dist, name);
  writeFileSync(path, readFileSync(path, "utf8").replaceAll("%APP_ORIGIN%", appOrigin));
}

const variable = join(pretendard, "web", "variable");
cpSync(join(variable, "pretendardvariable-dynamic-subset.css"), join(dist, "fonts", "pretendard.css"));
cpSync(join(variable, "woff2-dynamic-subset"), join(dist, "fonts", "woff2-dynamic-subset"), {
  recursive: true,
});
// The SIL Open Font License asks that the licence travel with the font files.
cpSync(join(pretendard, "LICENSE.txt"), join(dist, "fonts", "LICENSE.txt"));

console.log(`Built ${dist}\n  app origin: ${appOrigin}`);
