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
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

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
