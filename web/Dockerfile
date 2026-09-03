# syntax=docker/dockerfile:1.7

# Both base images are pinned by digest so a moved tag cannot change what ships.

# ---- build stage ---------------------------------------------------------
FROM node:26-alpine@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS build
WORKDIR /app

# Install deps first so source-only changes don't bust the npm cache layer.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --no-audit --no-fund

COPY . .
RUN npx ng build --configuration production

# ---- runtime stage -------------------------------------------------------
# Unprivileged variant: the master runs as uid 101, which cannot bind port 80.
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0 AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/web/browser /usr/share/nginx/html

EXPOSE 8080

# busybox wget — the nginx alpine images do not ship curl.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1
