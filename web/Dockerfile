# syntax=docker/dockerfile:1.7

# ---- build stage ---------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Install deps first so source-only changes don't bust the npm cache layer.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --no-audit --no-fund

COPY . .
RUN npx ng build --configuration production

# ---- runtime stage -------------------------------------------------------
FROM nginx:1.27-alpine-slim AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/web/browser /usr/share/nginx/html

EXPOSE 80
