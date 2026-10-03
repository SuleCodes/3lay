# Inbound email Worker

A Cloudflare Worker that receives emails through Cloudflare Email Routing and
forwards each one, as the raw message (`.eml`), to the Azure ingest function
in [`../function`](../function/README.md). The function stores it at:

```
<client>/<origin>/YYYY/MM/DD/<id>
```

- **client:** who the email was sent to. This is the address it was delivered
  to, so CC and BCC copies are filed correctly.
- **origin:** who sent it. This is the address in the `From` header, or the
  envelope sender if `From` can't be read.

| File | What it is |
|---|---|
| `worker.js` | The Worker. All the logic is in `handleEmail()`. |
| `wrangler.toml` | Worker name, entry point and the `FUNCTION_URL` variable. |
| `.dev.vars` | Local-only secrets for `wrangler dev`. Gitignored; you create it. |

## Current deployment

| | |
|---|---|
| Ingest email domain | `in.3lay.live`. Any address, e.g. `invoices@in.3lay.live` |
| Email Routing rule | Catch-all on `in.3lay.live` → Send to a Worker → `3lay-cell-82f0` |
| Cloudflare Worker | `3lay-cell-82f0` |
| Function App | `func-3lay-injest`, resource group `rg-3lay-prod` |
| `FUNCTION_URL` | `https://func-3lay-injest-fhcnhphjd9amf6fv.uksouth-01.azurewebsites.net` |
| Endpoint the Worker calls | `FUNCTION_URL` + `/api/ingest` |
| Storage | Storage account `rawinjest3layprod`, container `rawinjest3layprod` |

Only `@in.3lay.live` addresses are ingested. Mail to the root domain
(`@3lay.live`) matches no routing rule, so Cloudflare bounces it with
`550 5.1.1 Address does not exist` before the Worker runs. To ingest root
addresses too, add a catch-all on `3lay.live` that sends to the same Worker.

`name` in `wrangler.toml` must stay `3lay-cell-82f0`. `wrangler deploy`
updates the Worker with that name, and if the name doesn't match an existing
Worker, it creates a new one instead. If you rename the Worker in the
dashboard, change `name` too.

## What it needs

| Name | Type | Value |
|---|---|---|
| `FUNCTION_URL` | Variable, in `wrangler.toml` | Base URL of the Function App, ending in `.net`. **Don't add `/api/ingest`**: the Worker appends it, so including it would call `/api/ingest/api/ingest` and every email would fail with `404`. |
| `FUNCTION_KEY` | **Secret** | Function key for the `ingest` function, from Azure portal → Function App → Functions → `ingest` → Function Keys. |
| `INGEST_API_KEY` | **Secret** | The 3lay API key, sent as `X-3lay-Api-Key`. Must exactly match `FUNCTION:API_KEY` in App Configuration. |

The function needs both keys. The function key gets the request past Azure,
and the API key is checked by the function itself (see
[Authentication](../function/README.md#authentication)). Keep both as
secrets, never plain variables. Anyone holding them can write into any
client's folder.

If `INGEST_API_KEY` isn't set, the Worker throws on every email instead of
calling the function.

## Prerequisites

- Node.js. Wrangler, Cloudflare's CLI, runs through `npx`, so there's nothing
  to install.
- Access to the Cloudflare account, and a domain on it with **Email Routing**
  enabled. Enabling Email Routing adds the MX records it needs.

Log in once. This opens a browser:

```bash
npx wrangler login
```

Check which account you're on:

```bash
npx wrangler whoami
```

Run all commands below from this folder (`infra/cloudflare-worker`).

## First-time setup

1. **Check `wrangler.toml`.** It's already set for the current deployment
   (see above): `name = "3lay-cell-82f0"`, and `FUNCTION_URL` is the Function
   App's base `.net` URL. For a different Function App, copy its **Default
   domain** from the Function App's Overview page in the Azure portal, add
   `https://`, and leave off `/api/ingest`.

2. **Check nothing on the Worker needs keeping.** `wrangler deploy` replaces
   the Worker's entire code. If `3lay-cell-82f0` already does other things,
   check its current code first (dashboard → Workers & Pages →
   `3lay-cell-82f0` → **Edit code**), and see
   [Using it inside an existing Worker](#using-it-inside-an-existing-worker).

3. **Deploy the Worker:**

   ```bash
   npx wrangler deploy
   ```

4. **Add both keys as secrets.** Each command prompts for the value, so the
   keys don't end up in your shell history. First the function key, which is
   the `default` key under Function App → Functions → `ingest` → Function
   Keys. This is not the API key:

   ```bash
   npx wrangler secret put FUNCTION_KEY
   ```

   Then the API key, using the same value as `FUNCTION:API_KEY` in App
   Configuration:

   ```bash
   npx wrangler secret put INGEST_API_KEY
   ```

   Check both are there. This lists the names only, never the values:

   ```bash
   npx wrangler secret list
   ```

5. **Route email to the Worker.** In the Cloudflare dashboard, go to your
   domain → **Email** → **Email Routing** → **Routing rules**. Either:
   - **Custom address:** create an address (e.g. `invoices@in.3lay.live`)
     with the action **Send to a Worker**, then choose `3lay-cell-82f0`, or
   - **Catch-all:** set the catch-all action to **Send to a Worker** →
     `3lay-cell-82f0`, so every address on the domain is ingested.

6. **Send a real test email** to a routed address, then check it in the
   storage account under `<that address>/<your address>/YYYY/MM/DD/`.

## Updating the Worker

After changing `worker.js` or `wrangler.toml`:

1. Check it builds, without uploading anything:

   ```bash
   npx wrangler deploy --dry-run
   ```

2. Deploy:

   ```bash
   npx wrangler deploy
   ```

The new version is live as soon as the command finishes, and the email
routing rules keep pointing at it. Nothing needs redoing in the dashboard.

Things to know when deploying:

- **Secrets carry over.** `FUNCTION_KEY` and `INGEST_API_KEY` survive every
  deploy. You only set them again to change them.
- **Dashboard variables are wiped.** `wrangler deploy` replaces all plain
  variables with what's in `wrangler.toml`. If you add a variable in the
  dashboard, add it to `wrangler.toml` too, or deploy with `--keep-vars`.
- **Don't edit the code in the dashboard.** The next `wrangler deploy`
  overwrites it. Make changes in `worker.js` so they're in git.

### Changing the function key

Create the new key in Azure first. Then update the Worker. This takes effect
immediately, with no deploy needed:

```bash
npx wrangler secret put FUNCTION_KEY
```

Only then delete the old key in Azure, so there's no window where the Worker
holds a key that no longer works.

### Changing the API key

The function accepts only one API key at a time, so there's a short window
where emails fail. Do it at a quiet time, and do these steps back to back:

1. Generate a new key:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Update `FUNCTION:API_KEY` in App Configuration.
3. Restart the Function App (Azure portal → Function App → **Restart**). It
   only reads App Configuration at startup.
4. Update the Worker:

   ```bash
   npx wrangler secret put INGEST_API_KEY
   ```

Between steps 3 and 4, the function returns `401` and the Worker throws
rather than bouncing emails.

### Rolling back

List recent versions:

```bash
npx wrangler deployments list
```

Then go back to the previous one, or pass a version id to choose a specific
one:

```bash
npx wrangler rollback
```

### Using it inside an existing Worker

If emails already go to another Worker (e.g. your agent Worker), you can add
this one's logic to it instead of deploying it separately:

1. Copy `handleEmail()` and `senderAddress()` from `worker.js` into that
   Worker.
2. Call it from that Worker's `email()` handler:

   ```js
   export default {
     async email(message, env, ctx) {
       await handleEmail(message, env);
       // ...anything else that Worker does with the email
     },
   };
   ```

3. Give that Worker the same `FUNCTION_URL` variable and the `FUNCTION_KEY`
   and `INGEST_API_KEY` secrets, then deploy it the way you normally do.

An email routing rule sends to one Worker. If you merge the logic in, point
the rules at that Worker rather than `3lay-cell-82f0`.

## Watching it live

Stream the deployed Worker's logs:

```bash
npx wrangler tail
```

If the sender gets a bounce but `wrangler tail` shows **nothing**, Cloudflare
rejected the email before the Worker ran. The bounce says `550 5.1.1 Address
does not exist`. Check that the address is on `in.3lay.live`, and that the
domain's catch-all is **enabled** and set to **Send to a Worker** →
`3lay-cell-82f0`. The Email Routing activity log won't show these either.

Each email that reaches the Worker logs one line:
- `Ingested <id>: <origin> -> <client> (<n> bytes)`: stored.
- `Rejected <origin> -> <client>: ...`: the function refused the addresses,
  and the sender got a bounce.
- An exception: something on our side is wrong, and the email wasn't
  bounced.
  - `INGEST_API_KEY secret is not set`: add the secret.
  - `Ingest function returned 401` with `{"error": "Unauthorized"}`:
    `INGEST_API_KEY` doesn't match `FUNCTION:API_KEY`, or the Function App
    hasn't been restarted since the key changed.
  - `Ingest function returned 401` with an empty body: Azure rejected
    `FUNCTION_KEY`.
  - `Ingest function returned 404`: `FUNCTION_URL` probably ends in
    `/api/ingest`. It should be just the base `.net` URL.
  - `Ingest function unreachable` or `returned 5xx`: check `FUNCTION_URL`,
    and that the Function App is running.

## Testing locally

1. Start the ingest function locally. See steps 2–3 in
   [`../function/README.md`](../function/README.md).

2. Create `.dev.vars` in this folder:

   ```
   FUNCTION_KEY=local-dev
   INGEST_API_KEY=<your-api-key>
   ```

   `FUNCTION_KEY` can be anything, because the local function host doesn't
   check it. `INGEST_API_KEY` is checked even locally, so it must match
   `FUNCTION:API_KEY` in App Configuration.

3. Run the Worker, pointed at the local function:

   ```bash
   npx wrangler dev --var FUNCTION_URL:http://localhost:7071
   ```

4. Send it a test email. `from` and `to` are the envelope addresses, and the
   body is the raw email:

   ```bash
   curl -X POST "http://localhost:8787/cdn-cgi/local/email?from=ap@supplier.com&to=invoices@acme.3lay.dev" --data-binary @invoice.eml
   ```

   - `Worker successfully processed email` means it was stored.
   - `Worker rejected email` means the function refused the addresses; a real
     sender would get a bounce.

Local tests still write to the real storage account set in App Configuration.
