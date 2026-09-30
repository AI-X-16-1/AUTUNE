# syntax=docker/dockerfile:1
FROM node:22-slim

RUN corepack enable
WORKDIR /app

# Manifests first, so a source change does not reinstall every package.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/contracts/ts/package.json packages/contracts/ts/
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY apps/web apps/web
COPY packages/contracts/ts packages/contracts/ts

# Next inlines NEXT_PUBLIC_* and resolves the /api rewrite at build time, so
# both are build arguments: changing either means rebuilding this image.
# NEXT_PUBLIC_API_URL is the API as the *browser* reaches it (uploads and the
# live socket go there directly); API_PROXY_TARGET is the API as this
# container reaches it.
ARG NEXT_PUBLIC_API_URL
ARG API_PROXY_TARGET=http://api:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_TELEMETRY_DISABLED=1

RUN pnpm --filter @autune/web build \
 && chown -R node:node apps/web/.next

# next directly, not through pnpm: corepack fetched pnpm into root's cache at
# build time, and as `node` it would try to fetch it again on every start.
ENV NODE_ENV=production
USER node
WORKDIR /app/apps/web
EXPOSE 3000
CMD ["./node_modules/.bin/next", "start", "--port", "3000"]
