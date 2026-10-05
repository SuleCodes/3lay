# Planner: agentic workflows

## Context

The requirement is to build an agentic workflow for 3lay.

3lay aims to be an agent-focused platform for businesses and individuals who
have clients that operate via email, WhatsApp or other platforms that can be
orchestrated by 3lay.

The first client is **Rolepay**, a platform that helps actors understand their
income and how it breaks down before and after tax. Rolepay can't build backend
functionality themselves, so they need 3lay to ingest emails from their actors
and send the results back to them, e.g. to their webhook.

For now 3lay handles inbound email and notifies via webhooks. The vision is to
add many more inbound and outbound channels (WhatsApp, SMS, etc.).

### Two kinds of user

- **Client:** the business using 3lay (Rolepay, i.e. the 3lay user
  `rolepay-agent`). Onboarded once, through the frontend. Sees results and
  metrics.
- **End user:** the client's own customers (Rolepay's actors). They only send
  emails, and never see 3lay. 3lay will eventually build profiles for them,
  keyed by sender address (already stored as `origin`).

## The agents

The key decision: **only work that needs judgement uses an LLM.** Routing and
delivery are rules, so they're plain code. That makes them cheaper, faster,
predictable and testable.

| Name | Role | LLM? |
|---|---|---|
| **Orchy** | Orchestrator: the LangGraph graph itself | Only to classify documents; routing is deterministic |
| **Boardy** | Onboards clients through a conversation | Yes |
| **Obed** | Worker: extracts structured data from each request | Yes |
| **Justice** | Judge: scores Obed's output and gives a verdict | Yes |
| **Noti** | Delivers results to each configured destination | No |

### Orchy: orchestrator
Manages the end-to-end flow of each request. In LangGraph this is the graph:
nodes for each step, and conditional edges for routing (e.g. extraction
failed → report the failure, instead of continuing to Noti). It's triggered by
the ingest queue message, and also backs the frontend: the run dashboard and the
Boardy chat.

Its one LLM step is **classifying** each attachment as one of the client's
document types (or `unknown`), so each document is extracted with the right
schema. Everything else it does is deterministic. See step 1 of
"Design on paper".

### Boardy: onboarding agent
Holds a conversation with a new client to understand their use case, knowing
3lay's capabilities so it can help configure the integration. For Rolepay it
gathers information about the company as it relates to 3lay, asks the relevant
questions, and builds a profile of the company and its users.

**Its output is a structured, versioned configuration, not a free-text prompt.**
The client reviews and approves it, and Obed's prompt is built from it. That
keeps it testable and diffable, and stops one bad conversation from silently
corrupting every later extraction. (See "Client configuration" below.)

**What Boardy asks about.** Every configurable option is something Boardy must
find out, rather than assume from Rolepay:
- the document types the client expects, and what to extract from each
- the rules each document should satisfy
- whether documents need cross-referencing (analyses)
- whether related events form cases, and how to match them
- whether the client has its own references for cases
- how long until an inactive case closes (default 7 days)
- where and how results are delivered (destinations), and which events each
  destination receives
- retry limits, within 3lay's limits
- who may send (intake), and the maximum number of attachments

LangGraph concepts: `interrupt` (waiting for human input), checkpointers
(the conversation survives the client leaving and coming back), streaming.

### Obed: worker agent
The powerhouse of operations. It follows the client's configuration to process
each request. For Rolepay: an actor emails their payslips; Obed reads the
attachments (OCR, or a model that can read images) and produces structured JSON
of the actor's pay details, matching the schema the client approved.

Email content is untrusted data. Obed has no tools that can send anything
anywhere.

### Justice: judge (LLM as judge)
Scores every result, not only failures, since its verdict is what gives each
result a confidence indicator. Runs **after** deterministic checks:

1. **Checks (plain code):** valid against the schema; business rules, e.g.
   gross − deductions = net, tax is plausible for the gross, dates are valid.
   Free, instant, never hallucinate.
2. **Justice:** judges what rules can't, e.g. "does this extraction actually
   match the document?"

Output:
- a confidence score per field
- an overall verdict: `accepted`, `needs_review` or `failed`
- reasons written for people to read

Notes:
- Don't rely on Obed scoring its own confidence; LLM self-scores are poorly calibrated.
- Ideally use a different model from Obed, so they don't share blind spots.
- Justice must be evaluated too: check its scores against the labelled test set.
- It roughly doubles the LLM cost per request.

### Noti: notifications
Delivers the final result to each of the client's configured **destinations**.
Not every client wants a webhook (some will want SMS or another trigger), so
each channel is an adapter with the same interface: "send this result, report
delivered or failed".

- **Webhook** (Rolepay): structured JSON, HMAC-signed with a secret for each
  client, retried safely without duplicates (`event_id`). The URL is validated
  at onboarding so it can't point at internal addresses (SSRF).
- **Text channels** (SMS, etc.): need a message template per destination, since
  an SMS is a human sentence, not JSON. Start with deterministic templates.

The destination always comes from approved configuration, never from Obed's output.

## Decisions so far

- **Emails sent before onboarding is complete are rejected, with a reason.**
  Clients get a status (e.g. `onboarding → active → suspended`). The Worker's
  recipient check rejects any client that isn't `active`, with a bounce message
  that says why, before the email costs a Function call, storage or an LLM call.
- **End users never see 3lay.** Email end users don't get replies, for now.
  Chat bots (a later channel) talk to end users, but always as the client's own
  bot. Clients see results and metrics in the frontend.
- **Billing is per event, and an event is one orchestration request,** not one
  message: each email, or each time a chat conversation hands work to the
  pipeline.
- **Every event has an `event_id`:** the ID the ingest Function already
  generates. It travels through the queue message, every step of the graph, the
  database, the webhook payload and the frontend. A client can look up any event
  by its ID, including failures.
- **Failures are reported clearly,** including low confidence, so clients always
  know how much to trust a result.
- **The frontend has both** a run dashboard and a chat with Boardy.
- **Models are swappable through configuration.** The abstraction is
  LangChain's chat-model interface (`provider:model` strings), with a model set
  for each agent in App Config (e.g. `BACKEND:OBED_MODEL`,
  `BACKEND:JUSTICE_MODEL`). Hugging Face is one possible provider.
- **Data residency is configurable per client** (later). Some clients have no
  requirement (Rolepay); others do (e.g. a client that needs its data processed
  in Europe). The client's configuration states the requirement, e.g. `none`,
  `uk`, `eu` or `us`, and 3lay routes each model call to a deployment in that
  region. See "Data residency" under step 1.
- **Provider for learning:** roadmap step 1 starts with Hugging Face (free tier,
  synthetic documents only).
- **Pricing is per event:** clients are billed on the number of events created.
  Which events count (e.g. failures caused by 3lay, reprocessing) is to be
  decided with the client configuration.
- **Retry limits can be set by each client,** within limits set by 3lay.
- **Framework:** LangChain for talking to models (one interface for every
  provider, structured output); LangGraph for orchestration (Orchy's graph) and
  Boardy's conversation.
- **Deployment:** the pipeline runs as a Container Apps job triggered by the
  ingest queue; Boardy runs in the backend API. See "Deployment" under
  Architecture.
- **Way of working:** I (Seth) write the code; Claude reviews it and explains the concepts.

## Architecture

```
Email ─▶ Worker (reject if not active or over the cap) ─▶ Function (event_id, blob, queue)
                                                 │
                                                 ▼
                              Orchy (LangGraph)
                                 │
                                 ├─▶ classify  each attachment → a document type (LLM)
                                 ├─▶ Obed      extract each document → structured JSON
                                 ├─▶ checks    schema + rules, per document (code)
                                 ├─▶ analyses  across documents, if configured
                                 ├─▶ Justice   per-field confidence + verdict
                                 └─▶ Noti      channel adapters per destination
                                                 │
                         every step written to events / event_steps
                                                 ▼
                          Frontend: run dashboard + Boardy chat
```

### Deployment

Agents aren't services. Each agent is a role in a graph (a node or small
subgraph, with its own prompt and model), so what gets deployed is the program
that runs the graph. The models themselves stay hosted by the provider and are
called over HTTPS.

There are two programs, because the work comes in two shapes:

| | Pipeline: Orchy → Obed → Justice → Noti | Boardy |
|---|---|---|
| Triggered by | A queue message | A client chatting in the frontend |
| Shape | Background job, no one waiting | Request and reply, streamed back |
| Runs in | **A Container Apps job** (event-driven, scaled by the ingest queue) | **The backend** (`ca-3lay-api`), as new API endpoints |
| State | Postgres (events, configurations), Blob (documents) | Postgres (LangGraph's Postgres checkpointer, `app` schema) |

```
Function ─▶ queue ─▶ Container Apps job (pipeline graph) ─▶ destinations
                         │
                         ▼
                 Postgres (events, configs) · Blob (documents)
                         ▲
Frontend ─▶ ca-3lay-api (Boardy endpoints, conversation saved in Postgres)
```

Why a Container Apps job for the pipeline:
- Reuses the existing Container Apps environment, registry (`containerreg3lay`)
  and image build process.
- Starts per queue message and scales to zero, so it only costs money while
  processing.
- No problem with long runs (several LLM calls per document).

Rejected alternatives:
- **Queue-triggered Function:** time limits and Python packaging are awkward for
  a heavy agent pipeline.
- **A worker inside the backend API:** slow extractions would compete with API
  requests, and the API couldn't scale to zero.
- **LangGraph Platform** (LangChain's paid hosting): not needed; hosting
  ourselves is cheaper and teaches more.

Code: one `agents/` Python package with all agents and graphs. The pipeline job
runs it from its own entry point; the backend imports Boardy from it. Agents stay
separate modules, so one can be split into its own service later if it needs to
(e.g. heavy OCR libraries, or different scaling).

## Cross-cutting concerns

- **Prompt injection:** emails will eventually contain "ignore your instructions
  and…". Treat their content as data, give Obed no outbound tools, and take
  destinations only from configuration.
- **Personal data (UK GDPR):** payslips contain names, National Insurance
  numbers, pay and tax. 3lay is a data processor for its clients. Decide:
  - the LLM provider's data terms and where data is processed (e.g. Azure AI
    Foundry in UK South)
  - how long raw emails and results are kept
  - a data processing agreement with Rolepay
- **Model choice:** not every model handles structured output or tool calling
  well, and reading documents needs OCR or a model that can read images. A
  dedicated Hugging Face GPU endpoint costs far more than the budget. Pay-per-token
  providers are cheaper, but send data to third parties.
- **Evaluation:** a test set of 10–20 payslips (redacted or synthetic) with the
  correct JSON. It's what makes prompt and model changes safe: change one thing,
  rerun, compare scores. Tracing with LangSmith or Langfuse.
- **Reliability:** LLM calls fail and time out. Queue retries, the poison queue,
  and steps that are safe to re-run.

## Roadmap

Build from the inside out; each step teaches one concept.

| # | Step | What it teaches |
|---|---|---|
| 0 | **Design on paper** (done, see below) | Data modelling, contracts |
| 1 | Obed alone: one statement or payslip → validated JSON, LangChain only (no graph yet) | Prompting, structured output, OCR vs vision, swapping models |
| 2 | Wrap in LangGraph, add checks and a retry/fail edge | State, nodes, conditional edges |
| 3 | Add Justice | LLM as judge, evaluating the judge |
| 4 | Noti as a plain node: sign and POST the webhook | Mixing code and LLM steps |
| 5 | Trigger the graph from the queue; write events | Running agents in production |
| 6 | Run dashboard in the frontend | Events, timeline, metrics |
| 7 | Boardy | Human in the loop, checkpointers, streaming |

Rolepay works end to end after step 5, with a configuration written by hand.
Boardy then replaces the hand-written configuration.

## Design on paper

The three things defined before writing agent code. All agreed.

### 1. Client configuration
What Boardy will eventually produce, and what Orchy, Obed, Justice and Noti
read. Questions to answer:
- What does each agent need from it? (Obed: what to extract and how. Justice:
  the rules to check. Noti: where and how to deliver.)
- Which parts are structured fields, and which are free-text instructions?
- How is a change versioned and approved, and which version did each event use?
- Is there one configuration per client, or can a client have several workflows
  (e.g. payslips *and* contracts)?
- What can a client edit themselves, and what only through Boardy?

Draft (agreed):

#### How a client's work is described

Clients have one inbound address, and an email can hold several kinds of
document. Some clients need each document handled on its own (Rolepay); others
need all the documents in an email cross-referenced (e.g. checking an invoice
against its purchase order and delivery note). So instead of separate
"workflows", a configuration describes:

- **Document types:** the kinds of document the client expects. Each has a
  description (used to classify attachments), its own data schema and its own
  rules.
- **Analyses:** optional steps that work across all the documents in an event,
  e.g. cross-referencing. Each says which document types it needs, and has its
  own rules and/or instructions and output schema. Analyses run across the
  whole **case** (see "Cases" below), which can span several emails.

Every event runs the same pipeline, built from fixed building blocks:

```
classify each attachment ─▶ extract each document (by its type)
        ─▶ per-document checks ─▶ analyses across documents ─▶ judge ─▶ deliver
```

- **Classify:** Orchy's one LLM step. Each attachment becomes one of the
  client's document types, or `unknown` (a rejected item,
  `not_a_relevant_document`).
- In LangGraph this is a **fan-out / fan-in**: one branch per document (run in
  parallel), gathered back together for the analyses.

**All of it is configured by Boardy,** by choosing and filling in building
blocks: document types, schemas, rules from a fixed set of rule types, analyses
and destinations. Boardy can't invent new kinds of block or write code. When a
client needs something the blocks can't express, 3lay adds a new block type, and
every client can use it from then on.

#### Two layers

**Client account** (changes with the account; not versioned):
- status: `onboarding`, `active`, `suspended`
- billing plan and monthly event cap
- the active configuration version

**Configuration version** (generated by Boardy or edited in the dashboard,
approved by the client, never changed once approved):

| Section | Contents | Used by |
|---|---|---|
| **intake** | sender rules, accepted file types, maximum attachments | Worker / Function |
| **document_types** | per type: name, description (for classifying), schema, instructions, rules | Orchy, Obed, checks |
| **analyses** | cross-document steps: the document types they need, rules and/or instructions with an output schema | checks, Obed |
| **judging** | thresholds, e.g. confidence below 0.8 → `needs_review` | Justice |
| **destinations** | per destination: type (webhook, SMS…), settings, which events it receives, retry policy | Noti |
| **processing** | retry attempts, within 3lay's limits | Orchy |

Deliberately **not** in the configuration:
- **Model choice:** 3lay's decision, set in App Config. Clients choose *what*
  they get, not *how* 3lay produces it. The one exception is a **data residency
  requirement**: the client states the region, and 3lay picks the models (see
  below).
- **Secrets** (e.g. the webhook signing secret): stored separately and only
  referenced, so they're never copied into a version, shown in the dashboard or
  sent to Boardy.

#### Versions and approval
- Any change, from Boardy or from editing in the dashboard, creates a **new
  version**: `draft → approved → active`. Exactly one version is active.
- Both routes go through the same validation before the client can approve.
- Every event records the version it ran under. Reprocessing can use the new
  version or the original.

#### Rules as data
A small fixed set of rule types: `sum_equals`, `percentage_of`, `compare`
(A ≤ B), `date_within`, `required`, `allowed_values`. Each rule says what happens
when it fails: `needs_review` (a warning) or `reject` (a hard failure).

Within one document (Rolepay's commission check):

```json
{
    "id": "commission_matches_rate",
    "type": "percentage_of",
    "scope": "lines[]",
    "value": "deductions[category=agency_commission].amount",
    "percentage": "commission_rate",
    "of": "gross",
    "tolerance": 0.01,
    "on_fail": "needs_review",
    "message": "Commission doesn't match the stated rate"
}
```

Across documents, in an analysis (a hypothetical client matching invoices to
purchase orders):

```json
{
    "id": "invoice_total_matches_po",
    "type": "compare",
    "left": "items[document_type=invoice].data.total",
    "operator": "equals",
    "right": "items[document_type=purchase_order].data.total",
    "match_on": "po_number",
    "tolerance": 0.01,
    "on_fail": "needs_review",
    "message": "Invoice total doesn't match the purchase order"
}
```

#### Intake, billing and caps
- **Intake:** for now anyone can send to a client's address. Allowlists and
  "known end users only" are later options.
- **Billable events:** `completed` events, and failures caused by the sender
  (`no_documents_found`, `not_a_relevant_document`, …).
  **Not billed:** failures caused by 3lay (`processing_error`,
  `processing_timeout`). **Reprocessing:** billed if the client asks for it,
  free if 3lay starts it.
- **Monthly cap:** when a client reaches it, new emails are **bounced, with a
  reason**, until the next month or until the cap is raised. Bounced emails
  aren't events, so they aren't billed.

#### Rolepay's configuration, in these terms
- One document type, `income_statement`, with the `rolepay-income-statement`
  schema and its checks.
- No analyses.
- One webhook destination receiving `event.completed` and `event.failed`.
- Intake: anyone can send.

#### Cases: grouping events
Documents and messages that belong together can arrive separately: an invoice
this week and its purchase order next week, or an ongoing WhatsApp conversation
(a future channel, where a bot consults with people over many messages). So
every event belongs to a **case**, identified by a `case_ref` in the envelope.

- **Every event has a case.** By default each event starts a new one; an event
  joins an existing case when the client's **case matching** rules say so.
- **Analyses run on the whole case,** not just the latest event. When a new
  event joins, the case's analyses rerun across all its documents, and the
  result is delivered with the new event.
- **Case matching** is configured by Boardy, from fixed strategies:
  - **thread:** a reply in the same email thread (`In-Reply-To` /
    `References` headers), or the same chat conversation
  - **reference:** a reference written in the email, e.g. in the subject
  - **field:** an extracted value that matches, e.g. the same `po_number`
  - **sender and time window:** e.g. the same sender within 7 days
- **Cases are opened and closed:** closed explicitly, or after a period with no
  activity (client configurable, **default 7 days**), so a late email doesn't
  join a case that's long finished.
- **Client references (optional):** a client can supply its own reference for
  a case (e.g. its customer ID), so results line up with records in its own
  system. Boardy asks whether the client needs this.

**Rolepay doesn't use cases:** each email is its own case, and there's no client
reference.

#### What counts as an event
An **event is one orchestration request**: one time the pipeline is asked to do
work. It is not one message.

- **Email:** each email that passes intake is an event.
- **Chat (e.g. WhatsApp, later):** messages build up the case's conversation;
  an event is created only when the conversation hands work to the pipeline
  (e.g. a document to process, or a question that needs the client's data).
  Chat messages on their own aren't events and aren't billed.

Billing is per event, whichever case it joins.

#### Chat bots and end users
A chat bot talks directly to end users, but always **as the client's own bot**:
3lay stays invisible. For example, 3lay could be its own client, running a
WhatsApp bot that consults with its own prospective clients.

#### Data residency (later)
Some clients need their data processed in a particular region; others don't
mind. So residency is part of the client's configuration, as a **requirement**,
not a model choice:

- The configuration states `data_residency`: `none` (default), `uk`, `eu`
  or `us`. Boardy asks about it.
- 3lay keeps a **model catalogue per region** in App Config: for each agent and
  region, which deployment to use (e.g. Obed in `eu` → a model hosted in an EU
  region; `none` → whatever is best and cheapest).
- Orchy looks up the client's requirement and picks the matching deployment for
  every model call. Because every call goes through LangChain's common
  interface, the agents don't change: only the `provider:model` string and
  endpoint do.
- A region with no suitable model is a configuration error caught at approval,
  not a failure when an email arrives.

Example: Rolepay has no requirement, so it uses the default models. A client
that requires Europe has every call routed to EU-hosted deployments.

Things to resolve before offering it:
- **Models aren't the only place data lives.** Blobs (raw emails), the
  database (results) and logs and traces (e.g. LangSmith) also hold client data.
  A real residency promise means those are in the region too, e.g. a storage
  account and database per region.
- **Each provider's guarantees differ:** Azure regional deployments process
  data in the chosen region; Hugging Face Inference Providers depend on which
  partner serves the request, so a residency client must be pinned to a partner
  in that region (or use a dedicated endpoint).
- Residency deployments may cost more, which could be reflected in the plan's price.

#### Plans and costs
- Plans are priced on **events per month and documents per event**, and
  intake has a **maximum attachments** setting, so the cost of a large email
  is covered.
- **Warning before the cap:** at 80% of the monthly cap the client is warned
  (by email and in the dashboard), before their end users' emails start
  bouncing.

### 2. Event lifecycle
The states an event moves through, and which moves are allowed. Questions:
- What are the states? (e.g. `received → processing → extracted → judged → delivered`)
- Where can it fail, and what does each failure look like to the client?
- What's the difference between `failed` (we couldn't process it) and
  `needs_review` (we processed it but aren't sure)?
- What happens when delivery fails but extraction succeeded? Is it retried?
  How many times?
- What is recorded for each step (for the timeline in the frontend)?

Draft (agreed):

An event goes through three separate kinds of status, because each changes
independently:

| Question | Field | Values |
|---|---|---|
| How far did processing get? | event **status** | `received`, `processing`, `completed`, `failed` |
| How much should you trust each result? | item **verdict** (from the checks and Justice) | `accepted`, `needs_review`, `rejected` |
| Did the client get told? | **delivery status**, per destination | `pending`, `delivered`, `retrying`, `gave_up` |

#### Processing status

```
received ──▶ processing ──▶ completed
                 │  ▲
                 │  └── transient error: retry (up to 3 attempts)
                 │
                 └────▶ failed
                        (permanent error, out of retries, or stuck)
```

- **received:** the Function stored the email and queued it.
- **processing:** the graph is running; steps are recorded as they happen.
- **completed:** a result exists. Its items can have any verdict.
- **failed:** no usable result. Always has an error code.

Emails rejected at the Worker (unknown client, onboarding not finished) never
become events: they're bounced, with a reason, before anything is stored.

Two kinds of failure:
- **Transient** (LLM timeout, rate limiting, database briefly down): retried
  automatically with increasing waits, up to **3 attempts** by default. Only
  after the last one does the event become `failed` (`processing_error`).
- **Permanent** (no attachments, password-protected PDF, unsupported file, not a
  relevant document): retrying can't help, so it fails immediately.

A **watchdog** marks an event `failed` if it has been `processing` for too long
(e.g. the worker crashed mid-run), so a client always hears about every event.

#### Error codes
Part of the contract with clients, like the envelope: stable, documented, and
never renamed. Starting list:

| Code | Meaning | Whose problem |
|---|---|---|
| `no_documents_found` | The email had no attachments (or none that could be documents) | Sender |
| `unsupported_file_type` | An attachment type 3lay can't read | Sender |
| `document_unreadable` | Corrupt, password-protected or illegible | Sender |
| `not_a_relevant_document` | Readable, but not the kind of document the client expects | Sender |
| `processing_error` | 3lay failed, after retries | 3lay |
| `processing_timeout` | Stuck in processing; failed by the watchdog | 3lay |

The same codes are used for rejected items, e.g. one unreadable attachment
among three.

#### Item verdicts and partial success
- **accepted:** checks passed and Justice is confident.
- **needs_review:** a result exists, but a check was flagged or Justice is
  unsure; reasons are given per field.
- **rejected:** this document produced no usable result (with an error code).

An event only fails when **nothing** is usable. An email with three
attachments, one unreadable, is `completed` with two items `accepted` and one
`rejected`.

`needs_review` is **inform only** (for now): the result is delivered with the
flag and reasons, and the client decides what to do. Reviewing and correcting
results in 3lay is a possible later feature.

#### Delivery
- Tracked **per destination**, since a client can have several.
- Failed deliveries are retried with increasing waits (over e.g. 24 hours),
  then marked `gave_up`.
- The dashboard shows delivery failures, with a **resend** button.
- **Failed events are delivered too** (`event.failed`), not only successes.
- Retry limits have 3lay defaults; clients can configure them.

#### Reprocessing: events and runs
An event can be **reprocessed**, e.g. after the client's schema changes or a
better model becomes available. So an event has one or more **runs**:

- Each run goes through the full lifecycle, with its own steps and result.
- The latest run is the event's current result; earlier runs are kept as history.
- Every run is delivered, with its run number in the envelope, so the client
  knows a newer result replaces an older one for the same `event_id`.
- Automatic retries of transient errors are attempts *within* a run, not new runs.

#### What each step records
One record per step (extract, check, judge, deliver) per run, for the timeline
in the dashboard and for metrics:
- start and end times
- outcome, and error code and message on failure
- which model and schema version were used
- token usage and cost

### 3. Rolepay payload
The JSON Rolepay's webhook receives. Start from a real (redacted) payslip.
Questions:
- Which payslip fields matter to Rolepay (gross, net, tax, NI, pension,
  period, employer…)?
- How does an email with several payslips appear: one payload or several?
- Where do `event_id`, the verdict and per-field confidence go?
- What does a failure payload look like?
- How is the schema versioned when Rolepay's needs change?

Draft:

The payload is an envelope (the same for every client) around the data. The
data is mapped to a schema that Boardy defines during onboarding, from the
client's requirements.

The data is always a **list of items**, because one email can hold any number
of documents, and other clients' sources can't be predicted.

#### Envelope (revised)

```json
{
    "envelope_version": 1,
    "event_type": "event.completed",
    "event_id": "3f6c1a2e-8b4d-4c1e-9a7f-2d5b6e8c9f01",
    "run": 1,
    "case_ref": "c-7d2e9b41",
    "received_at": "2026-10-05T14:03:12Z",
    "finished_at": "2026-10-05T14:03:41Z",
    "sender": "sam.smith@example.com",
    "status": "completed",
    "summary": { "accepted": 1, "needs_review": 0, "rejected": 0 },
    "config_version": 1,
    "dashboard_url": "https://app.3lay.live/events/3f6c1a2e-8b4d-4c1e-9a7f-2d5b6e8c9f01",
    "items": [
        {
            "source": { "attachment": "statement-january.pdf", "page": 1 },
            "document_type": "income_statement",
            "schema": { "id": "rolepay-income-statement", "version": 1 },
            "verdict": "accepted",
            "confidence": {
                "overall": 0.94,
                "fields": { "gross": 0.99, "net": 0.98, "year_to_date_tax": 0.81 }
            },
            "data": { "...": "one object matching the client's schema" }
        }
    ],
    "analyses": [],
    "error": null
}
```

| Field | Why |
|---|---|
| `envelope_version` | Version of this envelope format: 3lay's contract with every client. Changes rarely. |
| `event_type` | Lets the receiver route without reading the body: `event.completed` or `event.failed`. |
| `case_ref` | The case this event belongs to. Events that belong together (a reply in the same thread, a matching reference, a chat conversation) share a `case_ref`; otherwise each event starts its own case. |
| `run` | Which run of the event this is. A higher run replaces an earlier result for the same `event_id`; `event_id` + `run` identifies a delivery, so retries can be safely ignored as duplicates. |
| `received_at` / `finished_at` | When the email arrived, and when this run finished (completed or failed). ISO 8601, UTC. |
| `status` | `completed` or `failed` (only finished runs are delivered). |
| `summary` | Count of items per verdict, so the receiver can route without reading every item. |
| `config_version` | Which version of the client's configuration this run used. |
| `dashboard_url` | Where the client sees this event in the frontend, especially for failures. |
| `items` | One entry per document found. Each wraps the client's data with 3lay's information about it. |
| `items[].document_type` / `schema` | What the document was classified as, and which of the client's Boardy-defined schemas (and which version of it) its `data` matches. Separate from `envelope_version`. |
| `items[].source` | Which attachment (and page) the item came from, for tracing a value back to the document. |
| `items[].verdict` / `confidence` | `accepted`, `needs_review` or `rejected`. A rejected item has an `error` (same codes as events) and `data` is null. Per item, so one clear payslip and one blurry one in the same email get different verdicts. Field paths are relative to the item's `data`, so they work for any schema. |
| `analyses` | Results of cross-document analyses across the whole case, if the client has any: one entry per analysis, each with its own verdict, confidence, `data` (matching the analysis's output schema) and rule outcomes. Empty for Rolepay. |
| `error` | Set when the whole event failed (`items` is then empty), e.g. `{ "code": "no_documents_found", "message": "…" }`. |

The HMAC signature goes in a request header, not in the body.

Status, verdict and error code values are defined in step 2 (event lifecycle).

#### Rolepay data: schema draft (`rolepay-income-statement`, version 1)

**This schema is written by hand as a stand-in for what Boardy will generate.**
Every client gets its own schema from Boardy during onboarding; Rolepay's is
just the first. Until Boardy exists, this one lets Obed, the checks and Noti be
built and tested. Afterwards it becomes Boardy's worked example and the quality
bar for what it generates. Nothing outside the client's configuration may
assume these fields exist.

What Obed is given: the shape of one item (one document), and in each
`description` the instruction for finding that value. Written as
[JSON Schema](https://json-schema.org/), a standard format that LangChain's
structured output accepts directly.

Actors receive two kinds of document, and one schema covers both:

- **Agency statement** (self-employed work, e.g. commercials and most screen
  work): an agent collects fees from the engagers, takes commission plus VAT on
  it, and pays the actor. One statement can cover several jobs. Usually no tax
  or NI is deducted.
- **Payslip** (PAYE employment, e.g. some theatre and TV): an employer pays a
  salary or fee, and deducts income tax, NI and so on. Has a tax code and
  year-to-date figures.

Both are modelled as a document with a list of **lines**, one per job or
payment. A payslip is simply a document with one line.

```json
{
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "rolepay-income-statement/1",
    "title": "Income statement",
    "description": "One document showing money paid to one person: either an agency statement or a payslip. Copy values exactly as printed on the document. If a value is not printed, use null. Never calculate, estimate or infer a value that isn't shown. Never extract bank account details, National Insurance numbers or addresses.",
    "type": "object",
    "additionalProperties": false,
    "required": [
        "document_type", "recipient", "issuer", "document_reference",
        "period_start", "period_end", "payment_date", "tax_year", "tax_code",
        "currency", "lines", "total_gross", "total_net",
        "year_to_date_gross", "year_to_date_tax"
    ],
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["agency_statement", "payslip", "other"],
            "description": "agency_statement: issued by an agent, showing fees received for the recipient minus the agent's commission. payslip: issued by an employer or payroll provider, showing pay minus income tax and National Insurance. other: anything else."
        },
        "recipient": {
            "type": ["string", "null"],
            "description": "Full name of the person being paid, as printed. On an agency statement this is often labelled 'client'."
        },
        "issuer": {
            "type": ["string", "null"],
            "description": "The organisation that issued the document: the agency for an agency statement, or the employer for a payslip. Usually shown in the letterhead."
        },
        "document_reference": {
            "type": ["string", "null"],
            "description": "The document's own number or reference, e.g. a statement number or payslip number."
        },
        "period_start": {
            "type": ["string", "null"],
            "format": "date",
            "description": "First day of the period the document covers, as YYYY-MM-DD. Labelled e.g. 'pay run' or 'pay period'. Null if only the end is shown."
        },
        "period_end": {
            "type": ["string", "null"],
            "format": "date",
            "description": "Last day of the period the document covers, as YYYY-MM-DD. Labelled e.g. 'pay run', 'period ending' or 'week ending'."
        },
        "payment_date": {
            "type": ["string", "null"],
            "format": "date",
            "description": "The date the recipient was paid, as YYYY-MM-DD, if one date applies to the whole document. Null if each line has its own date."
        },
        "tax_year": {
            "type": ["string", "null"],
            "pattern": "^\\d{4}-\\d{2}$",
            "description": "The UK tax year, written like 2025-26, only if printed. UK tax years run from 6 April to 5 April. Usually only on payslips."
        },
        "tax_code": {
            "type": ["string", "null"],
            "description": "The tax code exactly as printed, e.g. 1257L, BR, 0T or K475, including any suffix such as W1, M1 or X. Only on payslips."
        },
        "currency": {
            "type": "string",
            "pattern": "^[A-Z]{3}$",
            "description": "Three-letter currency code of the amounts, e.g. GBP, USD or EUR. Use GBP if the document shows £ or no currency and is from the UK."
        },
        "lines": {
            "type": "array",
            "minItems": 1,
            "description": "One entry per job or payment on the document. An agency statement lists one per job; a payslip has a single line for the whole pay.",
            "items": {
                "type": "object",
                "additionalProperties": false,
                "required": [
                    "engager", "production", "description", "fee_label", "fee_category",
                    "engager_reference", "invoice_reference", "payment_date", "received_date",
                    "gross", "vat_charged", "commission_rate", "deductions", "net"
                ],
                "properties": {
                    "engager": {
                        "type": ["string", "null"],
                        "description": "The company that hired the recipient for this work and paid for it, e.g. a production company or advertising agency. On a payslip, the employer."
                    },
                    "production": {
                        "type": ["string", "null"],
                        "description": "The production, show, brand or campaign the work was for, as printed."
                    },
                    "description": {
                        "type": ["string", "null"],
                        "description": "Any further description of the work as printed, e.g. 'Part 1', an episode or a performance week."
                    },
                    "fee_label": {
                        "type": ["string", "null"],
                        "description": "The type of fee exactly as printed, e.g. 'Buyout', 'Session fee', 'Salary'."
                    },
                    "fee_category": {
                        "type": "string",
                        "enum": ["session_fee", "buyout", "usage", "repeat_fee", "royalty", "salary", "holiday_pay", "expenses", "other"],
                        "description": "What kind of fee this is. Buyout: a one-off payment for usage rights. Usage or repeat_fee: further payments for the work being used or shown again. Session fee: payment for days worked. Salary: regular employment pay. Anything that doesn't fit: other."
                    },
                    "engager_reference": {
                        "type": ["string", "null"],
                        "description": "The engager's or payroll's reference for this payment, e.g. a 'pay ref'."
                    },
                    "invoice_reference": {
                        "type": ["string", "null"],
                        "description": "The invoice reference for this payment, e.g. an 'inv ref'."
                    },
                    "payment_date": {
                        "type": ["string", "null"],
                        "format": "date",
                        "description": "The date this line was paid to the recipient, as YYYY-MM-DD. Often labelled 'payment date' or 'tax point date'."
                    },
                    "received_date": {
                        "type": ["string", "null"],
                        "format": "date",
                        "description": "The date the agency received the money from the engager, as YYYY-MM-DD. Only on agency statements."
                    },
                    "gross": {
                        "type": "number",
                        "description": "The fee for this line before anything is taken off, in currency units with up to two decimal places (e.g. 7000.00)."
                    },
                    "vat_charged": {
                        "type": ["number", "null"],
                        "description": "VAT charged to the engager on top of the fee, because the recipient is VAT registered. Often labelled 'client VAT'. Use 0 if printed as zero, null if not shown."
                    },
                    "commission_rate": {
                        "type": ["number", "null"],
                        "description": "The agent's commission rate for this line as a percentage, e.g. 15 for 15%. Null if not printed."
                    },
                    "deductions": {
                        "type": "array",
                        "description": "Every amount taken off this line, one entry per deduction printed with an amount above zero. Empty if nothing was taken off.",
                        "items": {
                            "type": "object",
                            "additionalProperties": false,
                            "required": ["label", "category", "amount"],
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "The deduction's name exactly as printed, e.g. 'Comm.', 'VAT on com.', 'PAYE', 'Ee NIC'."
                                },
                                "category": {
                                    "type": "string",
                                    "enum": ["agency_commission", "vat_on_commission", "income_tax", "national_insurance", "pension", "student_loan", "other"],
                                    "description": "What kind of deduction this is. Commission taken by an agent: agency_commission. VAT charged on that commission: vat_on_commission. PAYE or income tax: income_tax. NI or NIC: national_insurance. Anything else, e.g. company or agency deductions: other."
                                },
                                "amount": {
                                    "type": "number",
                                    "description": "The amount deducted, as a positive number, in currency units with up to two decimal places."
                                }
                            }
                        }
                    },
                    "net": {
                        "type": "number",
                        "description": "The amount paid to the recipient for this line after deductions. Labelled e.g. 'payout', 'net pay' or 'take-home pay'."
                    }
                }
            }
        },
        "total_gross": {
            "type": ["number", "null"],
            "description": "The total gross for the whole document, only if printed. Do not add up the lines yourself."
        },
        "total_net": {
            "type": ["number", "null"],
            "description": "The total amount paid to the recipient for the whole document, only if printed, e.g. the amount paid to their bank account. Do not add up the lines yourself."
        },
        "year_to_date_gross": {
            "type": ["number", "null"],
            "description": "Total gross pay so far this tax year from this employer, as printed in a year-to-date section. Usually only on payslips."
        },
        "year_to_date_tax": {
            "type": ["number", "null"],
            "description": "Total income tax paid so far this tax year to this employer, as printed in a year-to-date section. Usually only on payslips."
        }
    }
}
```

Design choices:
- **One schema, a list of lines.** Agency statements list several jobs; a
  payslip is a single line. Rolepay's breakdown works the same way for both.
- **Copy, don't calculate.** Obed only extracts what's printed (totals are
  null unless printed). Calculations are for the deterministic checks, so a
  mismatch is caught instead of hidden.
- **Every field is required, but most can be null.** Required means "always
  include the key"; null means "not on this document". Strict structured-output
  modes need this, and it forces Obed to state that a value is missing rather
  than silently leave it out.
- **Label as printed, plus a fixed category** (for fees and deductions). The
  label is what's on the document; the category is what Rolepay builds its
  breakdown from, whatever each agency or payroll company calls things.
- **"No tax deducted" is a fact Rolepay relies on.** For self-employed work it
  means the actor must set money aside for Self Assessment. It's shown by the
  absence of `income_tax` deductions, and the check `gross − deductions = net`
  catches a tax line that Obed missed.
- **VAT on commission is its own category,** because for an actor who isn't
  VAT registered it's a real cost they can't reclaim.
- **Money is decimal currency units** (e.g. 7000.00), as printed, which is what
  a model reads most reliably. Code should handle the values as decimals, not
  floats.
- **Sensitive data is excluded by instruction:** no bank details, National
  Insurance numbers or addresses. Rolepay doesn't need them, so 3lay shouldn't
  hold them.

Deterministic checks this enables:
- Per line: `gross` + `vat_charged` − sum of `deductions` = `net` (to the penny)
- Per line: the `agency_commission` amount = `gross` × `commission_rate` ÷ 100
- Per line: `vat_on_commission` = 20% of the commission (UK standard rate;
  anything else is flagged for review, not failed)
- Sum of line `gross` = `total_gross`, and sum of line `net` = `total_net`, when printed
- `period_start` ≤ `period_end`; payment dates fall within or shortly after the period
- `payment_date` falls within `tax_year`, when both are printed
- `year_to_date_gross` ≥ the line's `gross`, and `year_to_date_tax` ≥ this
  period's income tax
- `tax_code` matches the pattern of a valid UK tax code
- An `agency_statement` without `agency_commission`, or a `payslip` without
  `income_tax` and `national_insurance`, is unusual: flag it for review

#### Rolepay data: examples

Synthetic examples (no real people, companies or references), as they would
appear in `items[].data`. These are also the start of the test set.

An agency statement with two jobs:

```json
{
    "document_type": "agency_statement",
    "recipient": "Sam Smith",
    "issuer": "Example Talent Ltd",
    "document_reference": "12",
    "period_start": "2025-12-22",
    "period_end": "2026-01-07",
    "payment_date": null,
    "tax_year": null,
    "tax_code": null,
    "currency": "GBP",
    "lines": [
        {
            "engager": "Northgate Creative Ltd",
            "production": "Acme Bank",
            "description": "Part 1",
            "fee_label": "Buyout",
            "fee_category": "buyout",
            "engager_reference": "10001",
            "invoice_reference": "5001",
            "payment_date": "2026-01-07",
            "received_date": "2025-12-29",
            "gross": 7000.00,
            "vat_charged": 0.00,
            "commission_rate": 15,
            "deductions": [
                { "label": "Comm.", "category": "agency_commission", "amount": 1050.00 },
                { "label": "VAT on com.", "category": "vat_on_commission", "amount": 210.00 }
            ],
            "net": 5740.00
        },
        {
            "engager": "Brightside Advertising Ltd",
            "production": "Fresh Foods",
            "description": "Part 3",
            "fee_label": "Buyout",
            "fee_category": "buyout",
            "engager_reference": "10002",
            "invoice_reference": "5002",
            "payment_date": "2026-01-07",
            "received_date": "2026-01-07",
            "gross": 7000.00,
            "vat_charged": 0.00,
            "commission_rate": 15,
            "deductions": [
                { "label": "Comm.", "category": "agency_commission", "amount": 1050.00 },
                { "label": "VAT on com.", "category": "vat_on_commission", "amount": 210.00 }
            ],
            "net": 5740.00
        }
    ],
    "total_gross": 14000.00,
    "total_net": 11480.00,
    "year_to_date_gross": null,
    "year_to_date_tax": null
}
```

A payslip:

```json
{
    "document_type": "payslip",
    "recipient": "Sam Smith",
    "issuer": "Riverside Theatre Productions Ltd",
    "document_reference": "PS-0042",
    "period_start": "2026-02-02",
    "period_end": "2026-02-08",
    "payment_date": "2026-02-13",
    "tax_year": "2025-26",
    "tax_code": "1257L",
    "currency": "GBP",
    "lines": [
        {
            "engager": "Riverside Theatre Productions Ltd",
            "production": "The Winter Garden",
            "description": "Week 3",
            "fee_label": "Weekly salary",
            "fee_category": "salary",
            "engager_reference": null,
            "invoice_reference": null,
            "payment_date": "2026-02-13",
            "received_date": null,
            "gross": 850.00,
            "vat_charged": null,
            "commission_rate": null,
            "deductions": [
                { "label": "PAYE", "category": "income_tax", "amount": 121.60 },
                { "label": "Ee NIC", "category": "national_insurance", "amount": 30.40 },
                { "label": "Pension", "category": "pension", "amount": 25.50 }
            ],
            "net": 672.50
        }
    ],
    "total_gross": 850.00,
    "total_net": 672.50,
    "year_to_date_gross": 18700.00,
    "year_to_date_tax": 2675.20
}
```

## Open questions

- Which LLM provider(s) for production? Learning starts on Hugging Face; the
  production choice depends on data residency and cost.
- End-user profiles: what goes in them, and when?
