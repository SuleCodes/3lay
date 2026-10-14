# 3lay agents

The agentic pipeline: Orchy (the LangGraph graph), Obed (extraction), the rule
engine, and Noti (signed webhook delivery). And Boardy, the onboarding agent a
client chats with to set up their configuration (run by the backend's API). The design and the decisions behind
it are in [tools/Planner.md](../tools/Planner.md).

```
agents/
  orchy/        the graph: state, nodes, routing, emails, envelope
  obed/         extraction: documents to image blocks, the model call
  rules/        the generic rule engine (rules are data, from the configuration)
  noti/         delivery: signing, destinations, retries
  boardy/       onboarding agent: tools, draft, samples, conversations for the API
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
| `AGENTS:BOARDY_MODEL` | Boardy's model (needs tool calling), e.g. `openai/gpt-oss-120b:groq` |
| `AGENTS:HF_TOKEN` | Hugging Face token (Key Vault reference) |
| `AGENTS:DATABASE_URL` | The pipeline's database connection, as the limited `agents_pipeline` role (below), not the backend's owner role (Key Vault reference). Without it, runs use the configuration in `tmp/` files. |
| `AGENTS:ONBOARDING_DATABASE_URL` | Boardy's database connection, as the `agents_onboarding` role (below): saves a configuration as a draft when the client approves it (Key Vault reference). |
| `AGENTS:CONVERSATION_ENCRYPTION_KEY` | Encrypts Boardy's saved conversations (Key Vault reference; `scripts/setup_conversations.py --generate-key`) |
| `AGENTS:SAMPLES_CONTAINER_NAME` | Optional: the container for samples clients upload to Boardy, in the ingest storage account (default `onboarding-samples`) |
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

**2b. Create Boardy's database role.** Boardy saves a client's configuration
as a draft when they approve it, so it needs to add configurations, which the
pipeline's role can't. It can't touch secrets or events, and can't activate
anything (that's `scripts/activate_configuration.py`, with the backend's
connection):

```sql
create role agents_onboarding with login password 'choose-another-strong-password';
grant usage on schema app to agents_onboarding;
grant select (id, username) on app.users to agents_onboarding;
grant select, insert on app.client_configurations to agents_onboarding;
```

Then add `AGENTS:ONBOARDING_DATABASE_URL` to App Configuration the same way as
`AGENTS:DATABASE_URL` (session pooler, user `agents_onboarding.<project-ref>`,
as a Key Vault reference).

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

## Boardy

Boardy is the onboarding agent: a client chats with it (the frontend's
`/setup`, through the backend's `/boardy/conversations` endpoints) and it
builds their configuration, reading their sample documents with Obed's model
and trying the draft on them. When the client approves it (their explicit
confirmation in the chat), Boardy saves it as a new version with its own
limited role, and the backend switches it on straight away
(`store/activation.py`: activates it and creates signing secrets for new
webhooks, shown once to the client). Boardy never handles secrets.

To look at or activate a draft by hand (e.g. one saved from the terminal):

```powershell
.venv\Scripts\python scriptsctivate_configuration.py rolepay-agent             # review
.venv\Scripts\python scriptsctivate_configuration.py rolepay-agent --activate  # then activate
```

In the terminal, without the API: `scripts/talk_to_boardy.py rolepay-agent
--samples tmp/samples/invented` (conversations kept in memory).

**Setup, once:**

1. Boardy's database role, `agents_onboarding`, and
   `AGENTS:ONBOARDING_DATABASE_URL`: step 2b above.
2. A key for saved conversations: run
   `scripts\setup_conversations.py --generate-key`, store it in Key Vault and
   add `AGENTS:CONVERSATION_ENCRYPTION_KEY` as a Key Vault reference to it.
3. The tables conversations are saved in: LangGraph's own, in the `boardy`
   schema, created with the backend's connection, which also lets
   `agents_onboarding` use them. Run it again after upgrading
   `langgraph-checkpoint-postgres`:

   ```powershell
   .venv\Scripts\python scripts\setup_conversations.py
   ```

4. The samples container, `onboarding-samples`, in the ingest storage account.
   The API's identity (and you, locally) need Storage Blob Data Contributor on
   it.
5. The backend's migrations (`boardy_conversations`), from `backend/`:
   `alembic upgrade head`.

