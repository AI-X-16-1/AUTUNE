# apps/landing

The public landing page at `https://autune.cloud`: what Autune is, how it works,
how it treats personal data, and a way into the app. Static HTML and CSS, no
framework, no script.

## Why it is not a page of `apps/web`

The landing page is the only place Autune may ever carry an ad (#1320), and an
ad script runs with the full rights of the page it is on. In the app, such a
script could read meeting records on screen and call `/api/*` with the
signed-in person's session (the API is proxied on the app's own origin,
`apps/web/next.config.ts`). So the ad stays here, on another origin.

The app is at `app.autune.cloud` (#1343): another origin, but the same site as
this page. A script here can set cookies for all of `autune.cloud`, and a
`SameSite=Lax` cookie rides along on requests it sends to the app. Three
things in the app close that, and must stay before an ad script lands here
(#1346):

- the session cookie is `__Host-autune_session`, which a sibling name cannot
  set or overwrite;
- the OAuth state cookies are `__Host-` too, so no script here can plant a
  state and sign a visitor into someone else's account;
- `OriginGuard` refuses a state-changing request or a live WebSocket handshake
  from any origin but the app's own.

## Rules this page keeps

- **No script, no cookie, no browser storage.** `src/_headers` sets
  `script-src 'none'`. The privacy policy promises no behavioural data and no
  outside analytics; a script lands only in the pull request that changes that
  wording, with every owner approving (#1320). Do not turn on Cloudflare Web
  Analytics or any other injected script for this project.
- **No redirect at the root.** The address registered with an ad network must
  serve the page itself (AdFit operating policy 5.4).
- **Copy states what the product does today**, the same rule as
  `apps/web/src/app/legal/content.ts`. The meeting record in the hero is made
  up and says so.
- **The ad slot, when it comes,** sits between two sections, away from every
  button, link and the header, in a box of fixed size (AdFit operating policy
  5.3.1). The place is marked in `src/index.html`.

## Build and preview

```bash
pnpm install                      # once; the build copies Pretendard from apps/web
node apps/landing/build.mjs       # writes apps/landing/dist
python -m http.server 8787 -d apps/landing/dist   # http://localhost:8787
```

Links into the app are written `%APP_ORIGIN%` in `src/` and filled in by the
build (`DEFAULT_APP_ORIGIN` in `build.mjs`, or `LANDING_APP_ORIGIN` for one
build). `_headers` and `404.html` only take effect on Cloudflare Pages.

## Deploy

Cloudflare Pages, direct upload of `dist/`: create a project, upload the
folder, then add `autune.cloud` as a custom domain. An apex domain needs the
zone on Cloudflare, so the domain's nameservers point at Cloudflare.
