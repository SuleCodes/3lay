# Ingest function

An Azure Function (Python, v2 programming model) with a single HTTP endpoint:

```
POST /api/ingest
```

The body can be anything; in practice it's a raw email (`.eml`). Three
headers are required:

| Header | Meaning | Example |
|---|---|---|
| `X-3lay-Api-Key` | Shared secret; must match `FUNCTION:API_KEY` | `<your-api-key>` |
| `X-3lay-Client` | Who the email was sent to | `invoices@acme.3lay.dev` |
| `X-3lay-Origin` | Who sent it | `ap@supplier.com` |

```
POST /api/ingest
X-3lay-Api-Key: <your-api-key>
X-3lay-Client: invoices@acme.3lay.dev
X-3lay-Origin: ap@supplier.com
Content-Type: message/rfc822

<raw email>
```

On each request, it:

1. Stores the body, byte-for-byte as received, in Blob Storage at
   `<container>/<client>/<origin>/YYYY/MM/DD/<id>`. For example:
   `invoices@acme.3lay.dev/ap@supplier.com/2026/10/02/6f1c...`. The request's
   `Content-Type` is kept.
2. Puts a small JSON job message on a Storage Queue pointing at that blob.
3. Returns `202 {"id": "<id>", "status": "queued"}`.

Both headers are lowercased, so `AP@Supplier.com` and `ap@supplier.com` share
a folder. They may only contain letters, digits, `@`, `+`, `-`, `_` and `.`,
must start with a letter or digit, and can be at most 254 characters. Each
becomes a folder in the blob path, so this stops a value like `../x` or
`a/b@x.com` from writing outside its folder. Plain ids such as `acct-123` are
accepted too.

| Response | When |
|---|---|
| `202` | Stored and queued |
| `400` | `X-3lay-Client` or `X-3lay-Origin` missing or invalid, or the body is empty. The `error` field says which. |
| `401` | `X-3lay-Api-Key` missing or wrong, or `FUNCTION:API_KEY` isn't configured. Checked before anything else. |
| `500` | Storage failed. Safe to retry. |

### Authentication

Every request needs two credentials:

1. **The Azure function key** (`x-functions-key` header, or `?code=` in the
   URL). Azure checks this before the code runs. Not checked when running
   locally.
2. **The 3lay API key** (`X-3lay-Api-Key` header), a shared secret you
   generate. The function compares it against `FUNCTION:API_KEY` in App
   Configuration. Unlike the function key, this is also checked locally, and
   you can rotate it without touching the Function App.

The API key check fails closed: if `FUNCTION:API_KEY` is missing or empty,
every request gets `401` and the function logs `API_KEY is not configured;
refusing all requests`. The comparison is constant-time, so response timing
can't be used to guess the key.

To generate a key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Store it as `FUNCTION:API_KEY` in App Configuration, and as the Worker's
`INGEST_API_KEY` secret (see the [Worker README](../cloudflare-worker/README.md)).
The two must match exactly; the key is case-sensitive.

### Who sets the headers

In production, nothing calls this function directly. Emails arrive through
Cloudflare Email Routing at the Worker in
[`../cloudflare-worker`](../cloudflare-worker/worker.js), which:

- sets `X-3lay-Client` to the address the email was delivered to (the
  envelope recipient, so CC and BCC copies are filed correctly),
- sets `X-3lay-Origin` to the address in the email's `From` header, falling
  back to the envelope sender if `From` can't be read,
- forwards the raw email with the function key and API key.

If the function returns `400`, the Worker bounces the email. If the function
is down, or returns `401` or `5xx`, the Worker throws instead of bouncing.
Those are problems on our side, so the failure is left to Email Routing
rather than sent to the sender as a permanent rejection.

Only the Worker has the function key and API key, which is what makes the
headers trustworthy here. Anyone holding both can write into any client's
folder, so never share either.

> **Chunked uploads arrive empty.** The Azure Functions Python host drops
> request bodies sent with `Transfer-Encoding: chunked`. Callers must send a
> `Content-Length`. The Worker takes care of this; if you call the function
> directly with a streamed body, it will see it as empty.

## How it's configured

The only setting the function needs is `APP_CONFIG_CONNECTION_STRING`.
Everything else comes from Azure App Configuration, under keys prefixed
`FUNCTION:`:

| Key | What it is |
|---|---|
| `FUNCTION:API_KEY` | Shared secret callers must send as `X-3lay-Api-Key`. Required: without it every request is refused. |
| `FUNCTION:INGEST_STORAGE_CONNECTION_STRING` | Storage account the payloads and queue live in. Alternatively, set `FUNCTION:INGEST_STORAGE_ACCOUNT_NAME` to connect with managed identity / `az login` instead of a key. |
| `FUNCTION:RAW_CONTAINER_NAME` | Blob container for raw payloads (default `raw-ingest`) |
| `FUNCTION:INGEST_QUEUE_NAME` | Queue that job messages go to (default `ingest-jobs`) |

The container and queue are created automatically on first use.

A real environment variable (or a value in `local.settings.json`) with the same
name, minus the prefix, overrides the App Configuration value. For example,
setting `INGEST_QUEUE_NAME` locally sends jobs to a different queue without
touching the shared config.

> Running locally uses whatever storage account App Configuration points at,
> so test requests land in that real account. Use a separate container or queue
> name override (see above) if you don't want test data mixed in.

## Running it locally

### Prerequisites (one-time)

- **Python 3.10–3.13.** Azure Functions doesn't support 3.14 yet. On Windows,
  check what you have with `py -0`.
- **Azure Functions Core Tools v4**, which provides the `func` command
  ([install guide](https://learn.microsoft.com/azure/azure-functions/functions-run-local)).
  Check it with `func --version`.
- **Node.js**, to run [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite),
  Microsoft's local storage emulator. The Functions host uses a storage account
  for its own internal state (`AzureWebJobsStorage`), and Azurite fills that
  role locally. Without it, the endpoint still works, but the host keeps
  logging health warnings. Your actual payloads still go to the account set in
  App Configuration.

### 1. Set up the environment (one-time)

From `infra/function`:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy local.settings.json.example local.settings.json
```

On macOS/Linux, use `python3.11 -m venv .venv`,
`.venv/bin/python -m pip install -r requirements.txt` and
`cp local.settings.json.example local.settings.json`.

Then open `local.settings.json` and paste the App Configuration connection
string into `APP_CONFIG_CONNECTION_STRING`. This file is gitignored; never
commit it.

### 2. Start Azurite

In its own terminal, from `infra/function`:

```powershell
npx --yes azurite --silent --location .azurite
```

The first run downloads Azurite, which takes a minute. It's ready once the
`.azurite/` folder appears (it prints nothing in `--silent` mode). Leave it
running. Its data goes in `.azurite/`, which is gitignored.

### 3. Start the function

In a second terminal, from `infra/function`:

```powershell
.venv\Scripts\activate
func start
```

On macOS/Linux, activate with `source .venv/bin/activate`.

When it's ready, you'll see:

```
Functions:

        ingest: [POST] http://localhost:7071/api/ingest
```

Function keys aren't enforced locally, so you can call it without one. The
**API key is enforced**, so every request needs `X-3lay-Api-Key`. When calling
the function directly you play the Worker's role, so you also set
`X-3lay-Client` and `X-3lay-Origin` yourself.

### 4. Send a test request

Copy the value of `FUNCTION:API_KEY` from App Configuration (Azure portal →
your App Configuration store → **Configuration explorer**) into a shell
variable, so you don't have to paste it into every command.

PowerShell:

```powershell
$apiKey = "<your-api-key>"
```

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:7071/api/ingest -Headers @{ "X-3lay-Api-Key" = $apiKey; "X-3lay-Client" = "invoices@acme.3lay.dev"; "X-3lay-Origin" = "ap@supplier.com" } -ContentType "message/rfc822" -Body "From: ap@supplier.com`r`nSubject: Invoice INV-001`r`n`r`nPlease find invoice INV-001."
```

bash / Git Bash (in PowerShell, write `curl.exe` instead of `curl`):

```bash
API_KEY="<your-api-key>"
```

```bash
curl -X POST http://localhost:7071/api/ingest -H "X-3lay-Api-Key: $API_KEY" -H "X-3lay-Client: invoices@acme.3lay.dev" -H "X-3lay-Origin: ap@supplier.com" -H "Content-Type: message/rfc822" --data-binary $'From: ap@supplier.com\r\nSubject: Invoice INV-001\r\n\r\nPlease find invoice INV-001.'
```

A successful request returns:

```json
{"id": "6f1c...", "status": "queued"}
```

To send a saved email or any other file, use `--data-binary` so the bytes are
sent unchanged:

```bash
curl -X POST http://localhost:7071/api/ingest -H "X-3lay-Api-Key: $API_KEY" -H "X-3lay-Client: invoices@acme.3lay.dev" -H "X-3lay-Origin: ap@supplier.com" -H "Content-Type: message/rfc822" --data-binary @invoice.eml
```

To check authentication, leave out the API key. This returns `401` with
`"Unauthorized"`:

```bash
curl -X POST http://localhost:7071/api/ingest -H "X-3lay-Client: invoices@acme.3lay.dev" -H "X-3lay-Origin: ap@supplier.com" --data-binary 'hello'
```

To check validation, send the key but leave out a header. This returns `400`
with `"Missing X-3lay-Client header"`:

```bash
curl -X POST http://localhost:7071/api/ingest -H "X-3lay-Api-Key: $API_KEY" -H "X-3lay-Origin: ap@supplier.com" --data-binary 'hello'
```

### 5. Check the results

Open the storage account from App Configuration in the Azure portal, or in
[Azure Storage Explorer](https://azure.microsoft.com/products/storage/storage-explorer/):

- **Blob containers → your container → `<client>/<origin>/YYYY/MM/DD/`:** a
  blob named after the returned `id`, containing exactly what you sent.
- **Queues → your queue:** a message like the one below. The portal shows it
  decoded; the raw message is base64, which is what a queue-triggered function
  expects.

```json
{
  "ingest_id": "6f1c...",
  "client": "invoices@acme.3lay.dev",
  "origin": "ap@supplier.com",
  "container": "raw-ingest",
  "blob_name": "invoices@acme.3lay.dev/ap@supplier.com/2026/10/02/6f1c...",
  "content_type": "message/rfc822",
  "size_bytes": 79,
  "received_at": "2026-10-02T14:42:10.588675+00:00"
}
```

### 6. Test through the email Worker (optional)

To test the whole path, run the Worker locally in front of the function. In
`infra/cloudflare-worker`, create a `.dev.vars` file (it's gitignored):

```
FUNCTION_KEY=local-dev
INGEST_API_KEY=<your-api-key>
```

`FUNCTION_KEY` can be anything locally. `INGEST_API_KEY` must match
`FUNCTION:API_KEY` exactly. Then start the Worker pointed at the local
function:

```bash
npx wrangler dev --var FUNCTION_URL:http://localhost:7071
```

Wrangler simulates an incoming email with a POST to
`/cdn-cgi/local/email`. `from` and `to` are the envelope addresses, and the
body is the raw email:

```bash
curl -X POST "http://localhost:8787/cdn-cgi/local/email?from=ap@supplier.com&to=invoices@acme.3lay.dev" --data-binary @invoice.eml
```

- `Worker successfully processed email` means it was stored.
- `Worker rejected email` means the function refused the addresses, and a
  real sender would get a bounce.
- An error mentioning `Ingest function returned 401` means `INGEST_API_KEY`
  in `.dev.vars` doesn't match `FUNCTION:API_KEY`.

## Deploying to Azure

This publishes the code in this folder to an existing Function App. Run all
commands from `infra/function`.

### Before the first deploy (one-time)

1. **Log in to Azure** and select the subscription the Function App is in:

   ```bash
   az login
   ```

   ```bash
   az account set --subscription "<subscription-name-or-id>"
   ```

2. **Check the Function App's runtime.** It must be a **Linux** Function App
   on Functions runtime **v4**, running **Python**. Ideally use 3.11, the
   version the local venv uses, so what you test is what runs:

   ```bash
   az functionapp config show --name <function-app-name> --resource-group <resource-group> --query linuxFxVersion
   ```

   This should print `"Python|3.11"`. Any version from 3.10 to 3.13 works.

3. **Set the one app setting the code needs.** In the Azure portal, go to
   Function App → **Settings** → **Environment variables** → **App settings**
   → **Add**:

   | Name | Value |
   |---|---|
   | `APP_CONFIG_CONNECTION_STRING` | The App Configuration connection string |

   Click **Apply** and confirm the restart. Use the portal rather than the CLI
   for this, so the connection string doesn't end up in your shell history.

   While you're on that page, check these two exist. Azure normally sets them
   when the Function App is created:
   - `FUNCTIONS_WORKER_RUNTIME` = `python`
   - `AzureWebJobsStorage`: the Function App's own storage account.

   Everything else, including `FUNCTION:API_KEY` and the storage connection,
   comes from App Configuration, not from app settings.

### Publish

```bash
func azure functionapp publish <function-app-name>
```

This uploads the folder and installs `requirements.txt` on Azure (a "remote
build"), so your local `.venv` isn't used. Files in `.funcignore` aren't
uploaded: `.venv/`, `local.settings.json` (so local settings never reach
Azure) and Azurite's data. It takes a minute or two, and ends by listing:

```
Functions in <function-app-name>:
    ingest - [httpTrigger]
        Invoke url: https://<function-app-name>.azurewebsites.net/api/ingest
```

To redeploy after changing the code, run the same command again.

### Check it works

1. Get the function URL including its key. Use this key as the Worker's
   `FUNCTION_KEY`:

   ```bash
   func azure functionapp list-functions <function-app-name> --show-keys
   ```

2. Send a test request. Copy the URL from step 1, including `?code=...`, into
   the command. Quote it, because of the `?` and `&`:

   ```bash
   curl -X POST "https://<function-app-name>.azurewebsites.net/api/ingest?code=<function-key>" -H "X-3lay-Api-Key: $API_KEY" -H "X-3lay-Client: invoices@acme.3lay.dev" -H "X-3lay-Origin: ap@supplier.com" -H "Content-Type: message/rfc822" --data-binary $'From: ap@supplier.com\r\nSubject: Deploy test\r\n\r\nHello from the deployed function.'
   ```

   A `202` means it's working. Check the blob as in step 5 above.

   - `401` with an **empty** body: the function key (`code=`) is wrong.
   - `401` with `{"error": "Unauthorized"}`: the function key was fine, but
     the API key doesn't match `FUNCTION:API_KEY`.

3. To watch live logs while you test:

   ```bash
   func azure functionapp logstream <function-app-name>
   ```

   For history and errors, use **Application Insights** on the Function App
   (Monitoring → Logs, or Investigate → Failures).

### After changing App Configuration

The function reads App Configuration once, at startup. A redeploy restarts
it, but if you only changed a value in App Configuration, restart the
Function App yourself:

```bash
az functionapp restart --name <function-app-name> --resource-group <resource-group>
```

### Using managed identity instead of a storage key

To stop using `FUNCTION:INGEST_STORAGE_CONNECTION_STRING`:

1. Function App → **Settings** → **Identity** → **System assigned** → **On**.
2. On the storage account, go to **Access control (IAM)** → **Add role
   assignment**. Give the Function App's identity **Storage Blob Data
   Contributor** and **Storage Queue Data Contributor**.
3. In App Configuration, add `FUNCTION:INGEST_STORAGE_ACCOUNT_NAME` with the
   storage account's name. Then delete
   `FUNCTION:INGEST_STORAGE_CONNECTION_STRING`, which takes priority while it
   exists.
4. Restart the Function App.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `func start` keeps logging `Process reporting unhealthy` / storage connection errors | Azurite isn't running (step 2). The endpoint still works without it, but start Azurite to stop the warnings. |
| `500 {"error": "Failed to store payload"}` | Check the `func` terminal for the stack trace. Usually `FUNCTION:INGEST_STORAGE_CONNECTION_STRING` is missing or wrong in App Configuration, or the storage key was rotated. |
| `RuntimeError: Set INGEST_STORAGE_CONNECTION_STRING or INGEST_STORAGE_ACCOUNT_NAME` | Neither storage key exists in App Configuration, or `APP_CONFIG_CONNECTION_STRING` is blank, so nothing was loaded. |
| Worker fails to start / pip can't build `pydantic-core` or similar | The venv was created with Python 3.14. Delete `.venv` and recreate it with 3.10–3.13. |
| `401 {"error": "Unauthorized"}` | `X-3lay-Api-Key` is missing or doesn't match `FUNCTION:API_KEY` (check for stray spaces or a newline when copying). If the function log says `API_KEY is not configured`, the key is missing from App Configuration, or `func start` was started before you added it, so restart it. |
| `400` with an `error` message | The request failed validation. See the response table at the top. |
| `400 Request body is empty` though you sent a body | The body was sent chunked (`Transfer-Encoding: chunked`). Send it with a `Content-Length`, e.g. `curl --data-binary @file`. |
| `404` | Wrong method or path. The endpoint only accepts `POST` at `/api/ingest`. |
| A config change in App Configuration isn't picked up | Config loads once at startup. Restart `func start` locally, or the Function App in Azure. |
| Deployed function returns `401` to every request, and the log says `API_KEY is not configured` | `APP_CONFIG_CONNECTION_STRING` isn't set in the Function App's app settings, so no config was loaded. |
| Deployed function fails on every request, and the logs show an App Configuration error at startup | `APP_CONFIG_CONNECTION_STRING` is set but wrong (bad copy, or the store was recreated), so the function can't start. |
| `func azure functionapp publish` says it can't find the app | Wrong subscription. Run `az account show` and switch with `az account set`. |
