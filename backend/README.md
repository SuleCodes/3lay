# Backend

FastAPI + SQLAlchemy, on Postgres hosted by Supabase. Schema changes are made
with Alembic migrations.

```
app/
  main.py        FastAPI app and routers
  config.py      Settings, loaded from Azure App Configuration (BACKEND:*)
  database.py    Engine, session, and the `app` schema setting
  models.py      Table definitions -- the source of truth for the schema
  routers/       API endpoints
migrations/
  env.py         Alembic setup: where the URL comes from, which schema it manages
  versions/      One file per schema change, applied in order
alembic.ini      Alembic settings (no connection string -- see below)
```

## Setup

From `backend/`, on Windows (on macOS/Linux use `.venv/bin/` instead of
`.venv\Scripts\`). Use Python 3.10–3.13; the pinned dependencies don't install
on 3.14.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
```

`requirements-dev.txt` is `requirements.txt` plus test tools (pytest). The
Docker image only installs `requirements.txt`.

Put the App Configuration connection string in `.env` as
`APP_CONFIG_CONNECTION_STRING`. That's the only local setting. Everything
else, including the database URL, comes from App Configuration under
`BACKEND:` keys (see `.env.example` for the list).

Then create the tables (see [Migrations](#migrations)) and start the API:

```powershell
.venv\Scripts\alembic upgrade head
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Or use **Backend + Frontend** in VS Code's Run and Debug panel, which runs
the same uvicorn command. It doesn't run migrations, so run
`alembic upgrade head` yourself after pulling schema changes.

## Sign-up and onboarding

1. **Sign-in link:** `POST /auth/request-link` emails a magic link. Opening
   it calls `GET /auth/verify`, which signs the user in and, on first use,
   creates their account. **No API key is created at this point.** The
   response includes `needs_username: true` until onboarding is done.
2. **Choose a username:** `POST /account/username` with
   `{"username": "rolepay-agent"}`. This is the onboarding step, and it:
   - saves the username, which can't be changed afterwards, because the
     client's end users will be forwarding mail to it;
   - gives the client their forwarding address:
     `{username}@{CLIENT_FORWARDING_DOMAIN}`, e.g.
     `rolepay-agent@in.3lay.live`;
   - creates their first API key, returning the raw key **once**.

   The username and key are saved together, so either both happen or neither
   does. While typing, the UI can call
   `GET /account/username-availability?username=...`, which returns
   `available`, a `reason` if not, and the resulting address.
3. **More keys:** `POST /api-keys` works only once a username is set (`409`
   before that).

`GET /auth/me` returns `username` and `forwarding_address`; both are `null`
until onboarding.

**Username rules** (`app/usernames.py`): 3–32 characters, lowercase letters,
digits and single hyphens, starting and ending with a letter or digit.
Input is lowercased, so `RolePay-Agent` is saved as `rolepay-agent`, and
usernames are unique regardless of case. Mail-system and 3lay names such as
`postmaster`, `admin`, `noreply`, `support` and `3lay` are reserved. The rules
are deliberately stricter than email allows, because the username also
becomes the client's blob folder name and the Worker's `X-3lay-Client`
header.

| Response from `POST /account/username` | When |
|---|---|
| `201` | Claimed. Body: the user, plus the new API key. |
| `409` | Username already taken, or this user already has one. |
| `422` | Breaks the username rules; `detail` says which. |

The forwarding address isn't stored. It's built from `username` and
`BACKEND:CLIENT_FORWARDING_DOMAIN`, a required setting that must match the
domain whose Cloudflare Email Routing catch-all sends to the ingest Worker.

## Internal endpoints (for the email Worker)

`GET /internal/recipients/{address}` answers "is this a client's forwarding
address?" for the Cloudflare email Worker, which bounces mail for anything
that isn't:
- **`200`** `{"address": ..., "username": ...}`: yes. The match is exact:
  `{username}@{CLIENT_FORWARDING_DOMAIN}`, case-insensitive. No
  plus-addressing, other domains or retired usernames.
- **`404`** `{"detail": "Unknown recipient"}`: no.
- **`401`**: missing or wrong `X-3lay-Internal-Key` header.

The key is `BACKEND:INTERNAL_API_KEY` in App Configuration. It holds the same
value as `FUNCTION:API_KEY`, because the Worker sends its one `INGEST_API_KEY`
secret to both. It's checked in constant time and fails closed: if
the setting is missing, every request gets `401`. These endpoints are left
out of the public API docs (`/docs`). To rotate it, change all three together (see "Changing the API key" in the
Worker README).

## Deleting an account

`DELETE /account` with `{"confirm_email": "<the account's email>"}`. This is
the **Delete account** button in the dashboard's Danger zone. It permanently
removes, in this order:

1. **Every stored email for the client:** all blobs under
   `<forwarding_address>/` in the ingest storage container. This runs first:
   if it fails, nothing else has been touched and the user can retry, and it
   avoids leaving documents behind with no account to delete them from.
2. **The database rows, in one transaction:** the user, their API keys
   (cascade) and their sign-in tokens.
3. **The session:** the cookie is cleared. Sessions on other devices stop
   working on their next request.

The **username is retired**: it goes into `app.retired_usernames`, which
holds only the name and never links back to the deleted user. Retired names
can't be claimed again. Otherwise someone new could take `rolepay-agent` and
start receiving documents that the old client's end users still forward
there. The same email can sign up again later as a new account, with a new
username.

| Response | When |
|---|---|
| `200` | Deleted. `deleted_stored_items` says how many stored emails were removed. |
| `400` | `confirm_email` doesn't match the account's email (case-insensitive). |
| `502` | Deleting the stored emails failed. Nothing was deleted; safe to retry. |
| `503` | Ingest storage isn't configured (see below). Nothing was deleted. |

The backend needs access to the ingest storage account for this:
`BACKEND:INGEST_STORAGE_CONNECTION_STRING` (or
`BACKEND:INGEST_STORAGE_ACCOUNT_NAME` for managed identity) and
`BACKEND:RAW_CONTAINER_NAME`. These duplicate the function's `FUNCTION:`
values, so **update both if the storage key is rotated**. Queue messages
already waiting for the deleted client aren't removed. A job processor
should treat a missing blob as "deleted, skip".

## Database

Supabase is used purely as a **Postgres host**. The backend connects directly
with SQLAlchemy and the psycopg driver. It doesn't use Supabase's REST API,
its client libraries (`SUPABASE_URL` / `SUPABASE_KEY` aren't needed), or
Supabase Auth; sign-in is the backend's own magic-link flow.

### Connection string

Stored as `BACKEND:DATABASE_URL` in App Configuration. To get it: Supabase
dashboard → your project → **Connect** → **Session pooler**.

- **Paste it as-is** (`postgresql://...`). The backend adds the psycopg driver
  itself (`postgresql+psycopg://`).
- **Replace `[YOUR-PASSWORD]` including the square brackets.** They only mark
  the placeholder. `:[secret]@` fails with "password authentication failed";
  `:secret@` is right.
- **Use the Session pooler** (port 5432, host `*.pooler.supabase.com`), not
  the "Direct connection" (`db.<ref>.supabase.co`). The direct one may be
  IPv6-only, and many networks, Azure included, can't reach it. The transaction
  pooler (port 6543) also works; the engine is configured for it, but prefer
  the session pooler.
- **URL-encode special characters** in the password (`@ : / ? # %`), or
  pick a password without them.
- SQLite is no longer supported. A non-Postgres URL stops the backend at
  startup with `DATABASE_URL must be a Postgres connection string`.

### Schemas: `app` vs `public`

Postgres groups tables into **schemas** (namespaces inside one database). A
Supabase project's database already contains several:

| Schema | Owned by | What's in it |
|---|---|---|
| **`app`** | **Us** | All of 3lay's tables: `users`, `api_keys`, `magic_link_tokens`, and Alembic's `alembic_version`. |
| `public` | Supabase default | Empty for us. **Don't put 3lay tables here.** |
| `auth`, `storage`, `realtime`, `extensions`, ... | Supabase | Supabase's own features. Never modify these. |

**Why not `public`:** Supabase automatically publishes every table in `public`
through its REST API (PostgREST), using the project's publishable key. That
key is designed to be shipped to browsers, so treat it as public. Any table
in `public` without Row Level Security policies can be read, and possibly
written, by anyone holding that key. Our tables hold user emails and API key
hashes, so they live in `app`, which the REST API doesn't expose.

How it's enforced in code:
- `app/database.py` sets `DB_SCHEMA = "app"` and gives `Base` a
  `MetaData(schema="app")`, so every model and foreign key lands in `app`
  automatically. **Don't set `schema=` on individual models.**
- `migrations/env.py` keeps Alembic's version table in `app`. It also
  filters autogenerate to the `app` schema only, so Alembic never proposes
  changes to, or drops of, Supabase's own schemas.

To browse the tables in the Supabase dashboard, open **Table Editor** and
switch the schema dropdown from `public` to `app`. **Don't** add `app` to the
**Exposed schemas** list in the project's Data API settings; that would
publish it through the REST API and undo the point of using it.

## Migrations

The tables are defined in `app/models.py`, and created and changed only by
the migration files in `migrations/versions/`. The backend never creates
tables on startup. Alembic uses the same `BACKEND:DATABASE_URL` as the app,
so migrations always run against the database the API uses, and the password
is never written into `alembic.ini`.

Run all commands from `backend/`.

### Apply migrations

Brings the database up to the latest schema. Run this on first setup, after
pulling changes that add migrations, and before deploying code that needs
them:

```powershell
.venv\Scripts\alembic upgrade head
```

It's safe to run repeatedly; already-applied migrations are skipped. On a new
database it creates the `app` schema first.

### Check where the database is

```powershell
.venv\Scripts\alembic current
```

This shows the revision the database is at, followed by `(head)` if it's up
to date. To list all migrations and their order:

```powershell
.venv\Scripts\alembic history
```

### Change the schema

1. **Edit the models** in `app/models.py`, e.g. add a column or a new model.
   New models only need to subclass `Base`; they land in `app`
   automatically.

2. **Generate a migration.** Alembic compares the models with the live
   database and writes the difference to a new file in `migrations/versions/`.
   The database must be reachable, and up to date (`alembic upgrade head`)
   before you run it:

   ```powershell
   .venv\Scripts\alembic revision --autogenerate -m "add clients table"
   ```

3. **Review the generated file before applying it.** Autogenerate is a
   starting point, not the final word:
   - A **renamed** column or table shows up as a drop plus an add, which
     loses the data. Rewrite it with `op.alter_column(..., new_column_name=...)`
     or `op.rename_table(...)`.
   - A new **NOT NULL column on a table with rows** needs a
     `server_default`, or a backfill step, or the migration fails.
   - Changes to column types, server defaults and constraints aren't always
     detected. Add them by hand if needed.
   - Check every operation passes `schema="app"`. Autogenerate adds it,
     because the models carry it.

4. **Apply it:**

   ```powershell
   .venv\Scripts\alembic upgrade head
   ```

5. **Commit the migration file** together with the model change. Others, and
   deployments, apply it with `alembic upgrade head`.

To write a migration by hand instead, e.g. for data changes, create an empty
one with `alembic revision -m "..."` (no `--autogenerate`) and fill in
`upgrade()` and `downgrade()`.

### Check models and database match

```powershell
.venv\Scripts\alembic check
```

This reports any difference between `app/models.py` and the database, such as
a model change with no migration. "No new upgrade operations detected" means
they match.

### Undo a migration

```powershell
.venv\Scripts\alembic downgrade -1
```

This runs the latest migration's `downgrade()`. It's useful in development
to fix a migration you just applied. Downgrades that drop tables or columns
**delete that data**, so don't use them to roll back production. Write a new
forward migration instead.

### Preview the SQL without running it

```powershell
.venv\Scripts\alembic upgrade head --sql
```

This prints the SQL a migration would run, without changing anything.

### Rules of thumb

- **Never edit a migration that has already been applied** to a shared
  database. Add a new one instead. Editing an old one won't re-run it, and
  databases will drift apart.
- **One logical change per migration**, with a descriptive `-m` message.
  Files are named `YYYY_MM_DD_HHMM-<rev>_<message>.py` so they sort by date.
- **Don't change tables through the Supabase dashboard.** The next
  autogenerate would see the difference and try to reverse it. All schema
  changes go through models and migrations.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `password authentication failed` | Password wrong in `BACKEND:DATABASE_URL`, often the `[ ]` from `[YOUR-PASSWORD]` left in. |
| Connection times out, or `could not translate host name` | You're using the direct `db.<ref>.supabase.co` string. Use the Session pooler string. |
| `DATABASE_URL must be a Postgres connection string` | `BACKEND:DATABASE_URL` is missing, empty or still SQLite. |
| `relation "app.users" does not exist` when calling the API | Migrations haven't been applied: run `alembic upgrade head`. |
| `alembic check` reports changes you didn't make | Someone changed the schema outside migrations (e.g. in the dashboard), or a model changed without a migration. |
| `Target database is not up to date` when generating a migration | Run `alembic upgrade head` first, then generate. |
| `prepared statement "..." already exists` | Shouldn't happen: the engine disables prepared statements for Supabase's pooler. If you create another engine, pass `connect_args={"prepare_threshold": None}`. |

## Deploying (container)

The backend ships as a container image, built from `Dockerfile`, and stored
in the Azure Container Registry **`containerreg3lay`**
(`containerreg3lay.azurecr.io`, resource group `rg-3lay-prod`). A container
host, such as Azure Container Apps, pulls the image from there and runs it.

| | |
|---|---|
| Image | `containerreg3lay.azurecr.io/3lay-backend:<tag>` |
| Tags | A UTC timestamp per build (e.g. `20261004-0853`), plus `latest` |
| Base | `python:3.11-slim`, running as a non-root user |
| Port | `8000` |
| Health check | `GET /health` → `{"status": "ok"}` |
| Config | **Only** the `APP_CONFIG_CONNECTION_STRING` env var. Everything else comes from App Configuration at startup. |

No `.env`, venv or local database goes into the image (`.dockerignore`), so
the image contains no secrets.

### Build and push

Docker isn't needed locally. `az acr build` uploads this folder (minus
`.dockerignore`) and builds the image in Azure. From `backend/`:

1. Log in. The account needs push rights on the registry (AcrPush, or
   Owner/Contributor):

   ```bash
   az login
   ```

2. Build and push, tagged with the current UTC time and as `latest`:

   ```bash
   az acr build --registry containerreg3lay --image 3lay-backend:$(date -u +%Y%m%d-%H%M) --image 3lay-backend:latest --no-logs .
   ```

   In PowerShell, use `$(Get-Date -AsUTC -Format yyyyMMdd-HHmm)` for the tag
   instead.

3. Check it finished. `Succeeded` means the image is in the registry. A build
   takes about a minute.

   ```bash
   az acr task list-runs --registry containerreg3lay --top 1 --query "[0].{run:runId,status:status}" -o table
   ```

**Why `--no-logs`:** on Windows, the Azure CLI crashes while streaming the
build log (`UnicodeEncodeError: 'charmap' codec ...`). The build itself
carries on in Azure, but the command reports an error. `--no-logs` avoids
this. To read a build's log, go to the portal: **Container registry →
Services → Tasks → Runs → (run)**.

To list the images in the registry:

```bash
az acr repository show-tags --name containerreg3lay --repository 3lay-backend --orderby time_desc -o table
```

### Release checklist

1. **Migrations first.** If the release includes new migrations, apply them
   before the new image starts serving: `alembic upgrade head`, from your
   machine as in [Migrations](#migrations). The container deliberately
   doesn't migrate on startup, because several replicas starting at once
   would race each other. New code must also tolerate the old schema until
   the migration runs, and the old code the new schema.
2. **Build and push** the image, as above.
3. **Point the container host at the new tag,** then check `GET /health`.
   Use the timestamp tag rather than `latest`, so you know exactly what's
   running and can roll back.

To **roll back**, point the host at the previous timestamp tag. The image is
still in the registry. If a migration has to be undone too, see
[Undo a migration](#undo-a-migration), but prefer a new forward migration.

### What the container host needs

This is the next step: creating the app in Azure that runs this image.

- **Image:** `containerreg3lay.azurecr.io/3lay-backend:<tag>`, with target port
  **8000** and external HTTPS ingress.
- **Registry access:** give the app a **managed identity** with the
  **AcrPull** role on `containerreg3lay`. Don't use the registry's admin
  username and password. Once nothing uses them, turn off **Admin user** on
  the registry.
- **Secret:** `APP_CONFIG_CONNECTION_STRING`, stored as a secret and exposed
  as that env var.
- **Health probe:** `GET /health` on port 8000.
- **Scaling:** minimum replicas **0** keeps it inside the free allowance,
  with a cold start of a few seconds after idle. Set it to **1** to avoid
  cold starts.
- **Config to update for the deployed URLs:** `BACKEND:FRONTEND_URL` (magic
  links and CORS) must be the deployed frontend's URL, e.g.
  `https://app.3lay.live`.
- **Before going live:** the session cookie needs `secure=True` once it's
  served over HTTPS (see the TODO in `app/routers/auth.py`).
