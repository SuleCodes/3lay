# 3lay

Any input in, structured data out — via agentic extraction and webhooks.

The repo currently has two working parts:

- **Accounts:** email-only sign-up and sign-in (magic link, no passwords), and
  API key management. A FastAPI backend and a Next.js dashboard.
- **Email ingestion:** emails sent to any `@in.3lay.live` address are stored
  raw in Azure Blob Storage, and queued for processing.

The extraction engine (turning stored inputs into structured data) and
outbound webhooks come next, on top of these.

```
backend/                  FastAPI + SQLAlchemy + Postgres (Supabase), migrations via Alembic
frontend/                 Next.js (App Router) + Tailwind
infra/function/           Azure Function: stores raw inputs in Blob Storage, queues a job
infra/cloudflare-worker/  Cloudflare Email Worker: receives inbound email, calls the function
.vscode/launch.json       Run and debug the backend and frontend together
```

The frontend never talks to a database directly. Every piece of data goes
through the backend API, so `backend/` is the single source of truth for what
the webapp (or any future client) can do.

Each folder under `infra/` and `backend/` has its own README with setup,
deployment and troubleshooting details.

## How auth works

There's no password. A user enters their email, the backend emails them a
short-lived, single-use magic link, and clicking it signs them in — creating
their account on first use. On that first sign-in, an API key is generated
automatically. That key (not the session) is what a user's own backend would
use to call the 3lay API later.

Two separate credentials exist by design:
- **Session cookie** (httpOnly JWT): how the logged-in browser talks to the
  dashboard.
- **API key** (`3lay_live_...`): how a user's own server would call the 3lay
  API programmatically. Only a hash is ever stored; the raw value is shown
  once, at creation.

### Sign-in emails (Resend)

Magic links are sent with [Resend](https://resend.com), using
`BACKEND:RESEND_API_KEY` and `BACKEND:EMAIL_FROM`.

- **No API key:** the link is printed to the backend console instead, so the
  flow works locally with no email setup.
- **Current state:** the API key is set, but `EMAIL_FROM` is still Resend's
  test sender (`onboarding@resend.dev`). That sender only delivers to the
  Resend account owner's address, and other addresses are refused. To open
  sign-up to everyone, verify `3lay.live` in Resend by adding its ~3 DNS
  records in Cloudflare (no nameserver change), then set `EMAIL_FROM` to an
  address on that domain.
- **If Resend refuses a send,** the reason is logged, and the request returns
  `503` with a clear message. No unused sign-in token is left behind.

## How email ingestion works

```
sender ──► anything@in.3lay.live
             │  Cloudflare Email Routing (catch-all on in.3lay.live)
             ▼
           Cloudflare Worker 3lay-cell-82f0
             │  POST /api/ingest + raw email (.eml)
             │  X-3lay-Client  = recipient address
             │  X-3lay-Origin  = sender address (From header)
             │  function key + X-3lay-Api-Key
             ▼
           Azure Function func-3lay-injest
             ├─► Blob:  <client>/<origin>/YYYY/MM/DD/<id>   (raw, byte-for-byte)
             └─► Queue: small JSON job pointing at the blob
```

- **Who it's for:** the *client* is the address the email was delivered to,
  so CC and BCC copies are filed correctly. The *origin* is the sender.
- **Authentication:** the function needs two credentials, the Azure function
  key and the 3lay API key (`FUNCTION:API_KEY`). Only the Worker holds both.
- **Errors:** if the function rejects the addresses (`400`), the Worker
  bounces the email. If the function is down or misconfigured, the Worker
  throws instead, so we don't bounce mail because of our own problem.
- **Not yet built:** checking that the recipient is a paying client (planned:
  a Workers KV allowlist kept in sync by the backend), and processing the
  queued jobs.

Details: [infra/function/README.md](infra/function/README.md) and
[infra/cloudflare-worker/README.md](infra/cloudflare-worker/README.md).

## Configuration

All config and secrets live in Azure App Configuration (store
`config-3lay-prod`). Each component needs exactly one setting,
`APP_CONFIG_CONNECTION_STRING`, and reads everything else from the store under
its own key prefix (the prefix is trimmed on load):

| Component | Where the connection string goes | Keys |
|---|---|---|
| Backend | `backend/.env`, or the app's env vars | `BACKEND:DATABASE_URL`, `BACKEND:JWT_SECRET`, `BACKEND:JWT_EXPIRE_MINUTES`, `BACKEND:MAGIC_LINK_EXPIRE_MINUTES`, `BACKEND:FRONTEND_URL`, `BACKEND:SESSION_COOKIE_NAME`, `BACKEND:RESEND_API_KEY`, `BACKEND:EMAIL_FROM` |
| Frontend | `frontend/.env.local`, or the build/host env | `FRONTEND:NEXT_PUBLIC_API_URL` |
| Ingest function | `infra/function/local.settings.json`, or the Function App's app settings | `FUNCTION:API_KEY`, `FUNCTION:INGEST_STORAGE_CONNECTION_STRING`, `FUNCTION:INGEST_QUEUE_NAME`, `FUNCTION:RAW_CONTAINER_NAME` |

The Cloudflare Worker is the exception. It can't read App Configuration, so
its settings live in Cloudflare: `FUNCTION_URL` in `wrangler.toml`, and the
`FUNCTION_KEY` and `INGEST_API_KEY` secrets.

- **Labels:** keys use the default (empty) label.
- **Precedence:** a real environment variable overrides the App Configuration
  value for the same key, and App Configuration overrides the env file.
- **Restarts:** each component reads App Configuration once at startup, so
  restart it after changing a value.
- **Key Vault:** secrets can be stored as Key Vault references. These resolve
  via `DefaultAzureCredential`: managed identity when deployed, `az login`
  locally.
- **Frontend at build time:** the frontend loads its values while building,
  so `NEXT_PUBLIC_*` keys need the connection string at build time. Keys
  without that prefix stay server-only and never reach the browser.

The env files hold only the connection string; their `.example` templates
list the optional local overrides. Real env files, `local.settings.json` and
`.dev.vars` are gitignored.

## Database

Postgres, hosted on Supabase, with the connection string in
`BACKEND:DATABASE_URL`. The tables live in the `app` schema rather than
`public`, so Supabase's public REST API can't reach them. They're defined in
`backend/app/models.py`, and created and changed only through Alembic
migrations. See **[backend/README.md](backend/README.md)** for the connection
string, the `app` vs `public` schema notes, and how to run and write
migrations.

## Deployed resources

| What | Where |
|---|---|
| Config store | Azure App Configuration `config-3lay-prod` |
| Database | Supabase project (Postgres 17, region eu-west-1), schema `app` |
| Ingest function | Azure Function App `func-3lay-injest` (resource group `rg-3lay-prod`, UK South) |
| Raw storage | Storage account `rawinjest3layprod`: container `rawinjest3layprod`, queue `ingest-jobs` |
| Email Worker | Cloudflare Worker `3lay-cell-82f0` |
| Ingest email domain | `in.3lay.live`, a catch-all routed to the Worker. Mail to `@3lay.live` itself is not ingested. |

The backend and frontend aren't deployed yet; they run locally.

## Running it locally

Use Python 3.10–3.13 for the Python virtual environments: the pinned
dependencies don't install on 3.14.

### Quick start (VS Code)

Do the one-time setup in the Backend and Frontend sections below first: the
venv, `npm install`, the env files and the migrations. Then open the Run and
Debug panel (Ctrl+Shift+D), pick **Backend + Frontend**, and press F5.

This starts the backend (http://localhost:8000) and the frontend
(http://localhost:3000) together, each in its own terminal, with the debugger
attached, so breakpoints work in both. Stopping either one stops both. The
configurations are in `.vscode/launch.json`, and each can also be run on its
own. The backend configuration needs the **Python Debugger** extension
(`ms-python.debugpy`).

To run each piece from a terminal instead, follow the steps below.

### Backend

```
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

On Windows, use `.venv\Scripts\` in place of `./.venv/bin/`.

Put the App Configuration connection string in `.env`. The backend needs a
Postgres database; there's no local fallback. `alembic upgrade head` creates
or updates the tables. Run it once at setup, and again whenever you pull new
migrations.

### Frontend

```
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Put the App Configuration connection string in `.env.local`. Visit
http://localhost:3000 and enter your email. While Resend is still using its
test sender, use the Resend account owner's email, or remove
`BACKEND:RESEND_API_KEY` to get the link in the backend's terminal instead.

### Ingest function and Worker

These run separately from the app. See
[infra/function/README.md](infra/function/README.md) for running the function
locally (with Azurite) and deploying it, and
[infra/cloudflare-worker/README.md](infra/cloudflare-worker/README.md) for
testing the Worker with `wrangler dev` and deploying it.

## What's next

- **Open up sign-in emails:** verify `3lay.live` in Resend and set
  `BACKEND:EMAIL_FROM` (see [Sign-in emails](#sign-in-emails-resend)).
- **Paid-client check:** client and subscription tables in the backend,
  synced to a Workers KV allowlist, so the Worker bounces email for
  addresses that aren't active.
- **Processing:** a queue-triggered worker that reads each stored input and
  runs the extraction workflow.
- **Webhooks:** delivering the structured output to the client's endpoint.
- **Deploy the backend and frontend.**
