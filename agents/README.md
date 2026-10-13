# 3lay agents

The agentic pipeline: Orchy (the LangGraph graph), Obed (extraction), the rule
engine, and Noti (signed webhook delivery). The design and the decisions behind
it are in [tools/Planner.md](../tools/Planner.md).

```
agents/
  orchy/        the graph: state, nodes, routing, emails, envelope
  obed/         extraction: documents to image blocks, the model call
  rules/        the generic rule engine (rules are data, from the configuration)
  noti/         delivery: signing, destinations, retries
  store/        the database: configurations, destination secrets (encrypted)
  evaluation/   scoring runs against gold standards, pricing
  scripts/      runners (run_orchy, evaluate, make_email, make_gold, ...)
  tests/        pytest; no real model, network or database
  tmp/          local, gitignored: fixtures, schema, rules, runs, gold standards
```

Deployed as a Container Apps job woken by the ingest queue: see
[DEPLOY.md](DEPLOY.md).

## Setup

From `agents/`, with Python 3.11:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest
```

Settings follow the same convention as the backend and the ingest function:
**Azure App Configuration is the source of truth**, with `AGENTS:` keys (the
prefix is trimmed, so `AGENTS:OBED_MODEL` becomes `OBED_MODEL`). Anything set
locally wins, for this machine only: a real environment variable, then
`agents/.env` (gitignored). See `settings.py`.

The only setting needed locally is `APP_CONFIG_CONNECTION_STRING` in
`agents/.env`. Key Vault references are resolved with your `az login` locally,
and with the job's managed identity when deployed.

| App Configuration key | For |
|---|---|
| `AGENTS:OBED_MODEL`, `AGENTS:OBED_BASE_URL` | Obed's model, through Hugging Face Inference Providers |
| `AGENTS:HF_TOKEN` | Hugging Face token (Key Vault reference) |
| `AGENTS:DATABASE_URL` | The pipeline's database connection, as the limited `agents_pipeline` role (below), not the backend's owner role (Key Vault reference). Without it, runs use the configuration in `tmp/` files. |
| `AGENTS:SECRETS_ENCRYPTION_KEY` (+ `_VERSION`) | Encrypts destination signing secrets (Key Vault reference). When the backend shows and rotates secrets, `BACKEND:SECRETS_ENCRYPTION_KEY` points at the same Key Vault secret. |
| `AGENTS:LANGSMITH_*` | Optional tracing (synthetic documents only) |

Also used:

| Setting | For |
|---|---|
| `BACKEND:DATABASE_URL` | Read by `scripts/save_configuration.py` only, which writes configurations with the backend's connection. `ADMIN_DATABASE_URL` in `agents/.env` overrides it. |
| `NOTI_SECRET_<DESTINATION_ID>` | Optional, local only: a destination's signing secret, instead of the database |

## Client configuration in the database

The tables are created by the backend's migrations; the pipeline only reads
and writes them.

**1. Apply the backend migrations** (`client_configurations`,
`destination_secrets`, and the event records: `events`, `event_sources`,
`event_steps`, `deliveries`), from `backend/`:

```powershell
.venv\Scripts\python -m alembic upgrade head
```

**2. Create the pipeline's database role.** It can only read what it needs
(usernames, configurations, secrets) and write the event records. In the
Supabase SQL editor, with a strong password of your own:

```sql
create role agents_pipeline with login password 'choose-a-strong-password';
grant usage on schema app to agents_pipeline;
grant select (id, username) on app.users to agents_pipeline;
grant select on app.client_configurations, app.destination_secrets to agents_pipeline;

-- Event records (migration 8d3f6b1e4a27). events is updated when a run
-- finishes, which needs select too; the rest are only ever added to.
grant select, insert, update on app.events to agents_pipeline;
grant insert on app.event_sources, app.event_steps, app.deliveries to agents_pipeline;
```

Deleting expired personal data (after 90 days) and the watchdog will run with
the owner's connection, not this role: it can never delete anything.

Then add `AGENTS:DATABASE_URL` to App Configuration: the session pooler
connection string with that role, ideally as a Key Vault reference. Supabase's
pooler expects the user as `agents_pipeline.<project-ref>` (the project ref is
in the dashboard URL and in `BACKEND:DATABASE_URL`).

**3. Make the encryption key,** store it as a Key Vault secret, and add
`AGENTS:SECRETS_ENCRYPTION_KEY` to App Configuration as a Key Vault reference
to it:

```powershell
.venv\Scripts\python scripts\save_configuration.py --generate-key
```

**4. Save a client's configuration** from the `tmp/` files (schema, rules,
`destinations.json`) as its next version, activated. It writes using
`BACKEND:DATABASE_URL`:

```powershell
.venv\Scripts\python scripts\save_configuration.py rolepay-agent
```

New destinations get a signing secret, stored encrypted and printed once.

**5. Run an email.** The client is the email's To address
(`rolepay-agent@in.3lay.live` gives `rolepay-agent`), or `--client`:

```powershell
.venv\Scripts\python scripts\run_orchy.py tmp\fixtures\invented.eml
```
