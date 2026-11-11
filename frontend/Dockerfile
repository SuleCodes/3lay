# 3lay frontend image: Next.js as a small Node.js server (output: "standalone").
#
# Runs as the Container App ca-3lay-web at https://app.3lay.live. Built in
# Azure from this folder, with the API's address baked in (NEXT_PUBLIC_*
# values are inlined into the browser bundle at build time):
#   az acr build --registry containerreg3lay --image 3lay-web:latest \
#     --build-arg NEXT_PUBLIC_API_URL=https://api.3lay.live --no-logs .
# See README.md -> "Deploying". .dockerignore keeps .env.local (with the App
# Configuration connection string) and node_modules out of the upload.

# Node 24, like local development. If `npm ci` below fails with "Missing: ...
# from lock file", the lock was written by an older npm that skipped some
# platform-specific packages: refresh it with
#   npx npm@latest install --package-lock-only
# (it only adds the missing entries) and commit it.
FROM node:24-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# The bundle must point at the deployed API, never a local one.
ARG NEXT_PUBLIC_API_URL
RUN case "$NEXT_PUBLIC_API_URL" in \
      https://*) ;; \
      *) echo "Build with --build-arg NEXT_PUBLIC_API_URL=https://... (got '$NEXT_PUBLIC_API_URL')"; exit 1 ;; \
    esac
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Dependencies first, so code-only changes reuse this cached layer. npm is
# pinned to the version package.json's "engines" requires (see .npmrc), so the
# lock file is always read by the same npm that wrote it.
COPY package.json package-lock.json ./
RUN npm install -g npm@11.21.2 && npm ci
COPY . .
RUN npx next build

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The standalone server, plus the static files it serves but doesn't copy itself.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

# Don't run as root (the node image has a "node" user).
USER node
EXPOSE 3000
CMD ["node", "server.js"]
