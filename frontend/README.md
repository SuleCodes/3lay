# Frontend

Next.js (App Router) + Tailwind. Every page runs in the browser and talks to
the backend API, so the app builds to a **plain static site**
(`output: "export"` in `next.config.ts`). It's hosted on **Azure Static Web
Apps (Free plan)** at `app.3lay.live`.

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

Routes end in `/` (`trailingSlash: true`); `/login` redirects to `/login/`.

### Static export rules

Because the app is a static export, these Next.js features **can't be used**:
route handlers (`app/**/route.ts`), server actions, `cookies()` and
`headers()`, rewrites, redirects and headers in `next.config`, proxy and
middleware, ISR, and `next/image` with the default loader. Fetch data from the
backend API in client components instead. `next dev` raises an error if one
of them is used. The full list is in
`node_modules/next/dist/docs/01-app/02-guides/static-exports.md`.

## Deploying

| | |
|---|---|
| Host | Azure Static Web App (Free plan), resource group `rg-3lay-prod` |
| URL | `https://app.3lay.live` (custom domain), plus the default `*.azurestaticapps.net` address |
| Build output | `out/` |
| API it calls | `FRONTEND:NEXT_PUBLIC_API_URL` from App Configuration, **baked in at build time** |

### Build

```bash
npm run build:deploy
```

This is a production build that **ignores the `.env.local` override** of
`NEXT_PUBLIC_API_URL` and uses App Configuration's value. The script
(`scripts/build-deploy.mjs`) then checks the output: it **refuses** if the
build references `localhost`, or contains no HTTPS API URL, so a local
setting can't accidentally ship.

Don't deploy the output of plain `npm run build`. It uses your `.env.local`.

### Deploy

The deploy command uploads `out/` to the Static Web App, using its
**deployment token** (Azure portal → the Static Web App → **Overview** →
**Manage deployment token**). Keep the token out of files and git: set it in
your terminal session, then deploy.

PowerShell:

```powershell
$env:SWA_CLI_DEPLOYMENT_TOKEN = "<deployment token>"
npm run deploy
```

bash:

```bash
export SWA_CLI_DEPLOYMENT_TOKEN="<deployment token>"
npm run deploy
```

`npm run deploy` runs the Static Web Apps CLI through `npx`; there's nothing
to install. The site updates within a minute. If the token leaks, reset it
from the same **Manage deployment token** screen.

### Release checklist

1. If the API URL changed, update `FRONTEND:NEXT_PUBLIC_API_URL` in App
   Configuration first. It's baked in at build time.
2. `npm run build:deploy`, and check it ends with `build:deploy OK`.
3. `npm run deploy`.
4. Open `https://app.3lay.live` and sign in.

### Hosting config

`public/staticwebapp.config.json` is copied into `out/`, and tells Static Web
Apps to:
- treat `/dashboard` and `/dashboard/` alike (`trailingSlash: auto`);
- serve `404.html` for unknown paths;
- send basic security headers.

### Sign-in needs the API on the same site

The session cookie is only sent when the frontend and the API are on the
**same site**: `app.3lay.live` and `api.3lay.live` are, but
`*.azurestaticapps.net` and `*.azurecontainerapps.io` aren't. So, for signing
in to work in production:
- `FRONTEND:NEXT_PUBLIC_API_URL` = `https://api.3lay.live`;
- `BACKEND:FRONTEND_URL` = `https://app.3lay.live`;
- the session cookie is marked `secure`.

Browsing the site via its default `*.azurestaticapps.net` URL works, but
signing in there won't.
