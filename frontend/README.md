# Frontend

Next.js (App Router) + Tailwind. Every page runs in the browser and talks to
the backend API with the session cookie. It runs as a small **Node.js server**
(`output: "standalone"` in `next.config.ts`) in an **Azure Container App**,
`ca-3lay-web`, at `app.3lay.live`, so pages can have dynamic routes such as
`/events/[id]` (the address in every webhook's `dashboard_url`).

> This version of Next.js has breaking changes from older ones. See
> `AGENTS.md`, and the docs in `node_modules/next/dist/docs/`.

## Running locally

```bash
cp .env.local.example .env.local
npm install
npm run dev
```

Put the App Configuration connection string in `.env.local`. Config comes from
App Configuration (`FRONTEND:*` keys), and anything set in `.env.local`
overrides it on this machine only. `NEXT_PUBLIC_API_URL=http://localhost:8000`
in `.env.local` keeps local dev on your local backend, while App
Configuration holds the deployed API URL. Restart `npm run dev` after
changing it.

Data still comes from the API in client components: the session cookie
belongs to `api.3lay.live`, so the Next.js server never sees it. Server-side
features (route handlers, `headers()` in `next.config`, dynamic routes) are
available, but nothing relies on the server knowing who's signed in.

## Deploying

| | |
|---|---|
| Host | Azure Container App `ca-3lay-web`, environment `cae-3lay-prod`, resource group `rg-3lay-prod` |
| URL | `https://app.3lay.live` (custom domain, managed certificate) |
| Image | `containerreg3lay.azurecr.io/3lay-web`, built by `Dockerfile` |
| Identity | `id-3lay-web` (user-assigned), with **AcrPull** on the registry only: the frontend needs no other Azure access |
| API it calls | `NEXT_PUBLIC_API_URL`, **baked in at build time** (a build argument) |

### Build and release

From `frontend/`, build the image in Azure (no local Docker needed). The API
address is passed in; the Dockerfile refuses anything but `https://`, so a
local address can't ship:

```bash
az acr build --registry containerreg3lay --image 3lay-web:latest --build-arg NEXT_PUBLIC_API_URL=https://api.3lay.live --no-logs .
```

Then roll the app onto it (a new revision; the old one keeps serving until
the new one is ready):

```bash
az containerapp update -n ca-3lay-web -g rg-3lay-prod --image containerreg3lay.azurecr.io/3lay-web:latest
```

`.dockerignore` keeps `.env.local` (it has the App Configuration connection
string), `node_modules` and build output out of the upload. Its folder rules
have no trailing `/`: `az acr build` ignores those.

Locally, the same production build runs with:

```bash
NEXT_PUBLIC_API_URL=https://api.3lay.live npx next build
```

then `node .next/standalone/server.js` after copying `.next/static` and
`public` into `.next/standalone` (the Dockerfile does this).

### Hosting config

Security headers are set in `next.config.ts` (`headers()`). `/dashboard/`
redirects to `/dashboard`, so links from the old static site keep working.

### Sign-in needs the API on the same site

The session cookie is only sent when the frontend and the API are on the
**same site**: `app.3lay.live` and `api.3lay.live` are, but
`*.azurecontainerapps.io` isn't. So, for signing in to work in production:
- the image is built with `NEXT_PUBLIC_API_URL=https://api.3lay.live`;
- `BACKEND:FRONTEND_URL` = `https://app.3lay.live`;
- the session cookie is marked `secure`.

Browsing the app via its default `*.azurecontainerapps.io` address works,
but signing in there won't.
