# Deploying the agents (ingest job)

Orchy runs as a **Container Apps job**, `caj-3lay-orchy`, that the ingest
queue wakes up. It scales to zero: nothing runs, and nothing is charged,
while no emails arrive.

```
Email → Worker → ingest Function → blob (.eml) + queue message (ingest-jobs)
                                                  │  (KEDA checks every 30 s)
                                                  ▼
                              caj-3lay-orchy  ──  python -m jobs.ingest
                              downloads the blob, runs process_event
                              (configuration → record → graph → record),
                              deletes the message once recorded
```

Messages that fail 5 times, or are for a client with no active configuration,
move to `ingest-jobs-poison`.

The commands are for **Git Bash**, run from the repo root, signed in with
`az login`. Each step says what it changes.

## 0. Before you start

- The E4 and E5 migrations are applied, and the `agents_pipeline` role has its
  grants (`agents/README.md`).
- Rolepay has an active configuration in the database:
  `agents/.venv/Scripts/python.exe agents/scripts/save_configuration.py rolepay-agent`.
  Without it, every Rolepay message goes to the poison queue.
- The `AGENTS:` keys in App Configuration resolve (`HF_TOKEN`,
  `DATABASE_URL`, `SECRETS_ENCRYPTION_KEY` as Key Vault references,
  `OBED_MODEL`, `OBED_BASE_URL`).

```bash
# Git Bash rewrites anything starting with "/" as a Windows path, which breaks
# Azure resource IDs ("/subscriptions/..."): az then fails with
# "MissingSubscription". This switches that off for the session.
export MSYS_NO_PATHCONV=1

RG=rg-3lay-prod
LOCATION=uksouth
ENVIRONMENT=cae-3lay-prod
ACR=containerreg3lay
STORAGE=rawinjest3layprod
QUEUE=ingest-jobs
KEYVAULT=vault3lay
APPCONFIG=config-3lay-prod
IDENTITY=id-3lay-orchy
JOB=caj-3lay-orchy
```

## 1. Storage settings in App Configuration

Adds three plain (non-secret) keys the job reads. The container name is copied
from the ingest function's own setting, `FUNCTION:RAW_CONTAINER_NAME`.

```bash
az appconfig kv set --name $APPCONFIG --key AGENTS:INGEST_STORAGE_ACCOUNT_NAME --value $STORAGE --yes
az appconfig kv set --name $APPCONFIG --key AGENTS:INGEST_QUEUE_NAME --value $QUEUE --yes
# The container must be the one the ingest function writes to: copy its setting.
RAW_CONTAINER=$(az appconfig kv show --name $APPCONFIG --key FUNCTION:RAW_CONTAINER_NAME --query value -o tsv)
az appconfig kv set --name $APPCONFIG --key AGENTS:RAW_CONTAINER_NAME --value "${RAW_CONTAINER:-raw-ingest}" --yes
```

The job refuses messages pointing at any other container (they go to the
poison queue as "Unexpected container"), so these two settings must match.

## 2. The job's identity

Creates a user-assigned managed identity. It's created first so it can be
given its permissions before the job exists, and the job works on its first run.

```bash
az identity create --resource-group $RG --name $IDENTITY --location $LOCATION
IDENTITY_ID=$(az identity show --resource-group $RG --name $IDENTITY --query id -o tsv)
PRINCIPAL_ID=$(az identity show --resource-group $RG --name $IDENTITY --query principalId -o tsv)
CLIENT_ID=$(az identity show --resource-group $RG --name $IDENTITY --query clientId -o tsv)
```

## 3. The poison queue

Creates `ingest-jobs-poison` (the job's identity can use it, but not create it).
`--auth-mode key` uses the account key through your Azure login, because your
user has no storage data role.

```bash
az storage queue create --name $QUEUE-poison --account-name $STORAGE --auth-mode key
```

## 4. Permissions, and nothing more

Grants the identity exactly what the job needs. Role assignments can take a
few minutes to take effect.

```bash
STORAGE_ID=$(az storage account show --name $STORAGE --resource-group $RG --query id -o tsv)

# Pull the image from the registry.
az role assignment create --assignee-object-id $PRINCIPAL_ID --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope $(az acr show --name $ACR --query id -o tsv)

# Read the raw emails (read only).
az role assignment create --assignee-object-id $PRINCIPAL_ID --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Reader" --scope $STORAGE_ID

# Receive and delete messages on the ingest queue, and add them to the poison
# queue. Scoped to those two queues only, not the whole account. This also
# lets the queue trigger read how many messages are waiting.
for q in $QUEUE $QUEUE-poison; do
  az role assignment create --assignee-object-id $PRINCIPAL_ID --assignee-principal-type ServicePrincipal \
    --role "Storage Queue Data Contributor" --scope $STORAGE_ID/queueServices/default/queues/$q
done

# Read secrets in Key Vault (the Key Vault references in App Configuration).
# vault3lay uses access policies, so this is a policy, not a role.
az keyvault set-policy --name $KEYVAULT --object-id $PRINCIPAL_ID --secret-permissions get
```

App Configuration itself is read with a connection string (step 7), so the
identity needs no role there.

## 5. Build the image

Builds in Azure from `agents/` and pushes to the registry. `.dockerignore`
keeps `tmp/` (real-ish documents), `.env`, tests and scripts out of the upload.
`--no-logs` avoids the Windows CLI crash while streaming build logs (see the
backend README); check the result with `az acr task list-runs`.

```bash
cd agents
az acr build --registry $ACR --image 3lay-agents:$(date -u +%Y%m%d-%H%M) --image 3lay-agents:latest --no-logs .
cd ..
az acr task list-runs --registry $ACR --top 1 -o table
```

## 6. Clear the backlog

Agreed: the job starts fresh rather than processing every test email sent
since ingestion went live. The emails stay in Blob Storage; only the queue
messages are removed. Do this just before step 7.

```bash
az storage message clear --queue-name $QUEUE --account-name $STORAGE --auth-mode key
```

## 7. Create the job

The job is created from its definition,
[`deploy/caj-3lay-orchy.template.yaml`](deploy/caj-3lay-orchy.template.yaml),
rather than from command-line options, because the queue trigger's managed
identity can only be set in the definition. The commands fill in its
placeholders into a temporary copy outside the repo, create the job, and
delete the copy (it holds the connection string).

The App Configuration **read-only** connection string goes in as a Container
Apps secret: the job only reads configuration. `AZURE_CLIENT_ID` tells
`DefaultAzureCredential` which identity to use (for Key Vault and storage).

```bash
APPCONFIG_CS=$(az appconfig credential list --name $APPCONFIG   --query "[?name=='Primary Read Only'].connectionString | [0]" -o tsv)
ENVIRONMENT_ID=$(az containerapp env show --name $ENVIRONMENT --resource-group $RG --query id -o tsv)
JOB_YAML=$(mktemp --suffix .yaml)

sed -e "s|__IDENTITY_ID__|$IDENTITY_ID|g" -e "s|__ENVIRONMENT_ID__|$ENVIRONMENT_ID|g"     -e "s|__STORAGE__|$STORAGE|g" -e "s|__QUEUE__|$QUEUE|g" -e "s|__ACR__|$ACR|g"     -e "s|__CLIENT_ID__|$CLIENT_ID|g" -e "s|__APPCONFIG_CS__|$APPCONFIG_CS|g"     agents/deploy/caj-3lay-orchy.template.yaml > "$JOB_YAML"

# cygpath: with MSYS_NO_PATHCONV set, the /tmp path must be converted for az by hand.
az containerapp job create --name $JOB --resource-group $RG --yaml "$(cygpath -w "$JOB_YAML")"
rm "$JOB_YAML"
```

What the settings mean:

| Setting | Why |
|---|---|
| `triggerType: Event`, `minExecutions: 0` | Starts only when there are messages; scales to zero |
| `pollingInterval: 30` | Checks the queue every 30 seconds, so an email starts processing within about 30 s |
| `maxExecutions: 5` | Up to 5 executions at once when several emails arrive together |
| `replicaTimeout: 1800` | 30 minutes per execution: enough for extraction retries and delivery waits (each message is hidden from other executions for 30 minutes while it's worked on) |
| `replicaRetryLimit: 0` | Container Apps doesn't retry a failed execution itself: the queue does (a message that wasn't deleted reappears), with the poison queue after 5 attempts |
| the rule's `identity` | The queue trigger checks the queue with the job's identity, so no storage key is stored |

**If the queue trigger never starts executions** (messages wait, `az containerapp
job execution list` stays empty), the trigger may not be able to use the
identity in your region yet. The fallback is a storage connection string for
the trigger only, stored as a job secret:

```bash
STORAGE_CS=$(az storage account show-connection-string --name $STORAGE --resource-group $RG -o tsv)
az containerapp job secret set --name $JOB --resource-group $RG --secrets "queue-connection=$STORAGE_CS"
az containerapp job update --name $JOB --resource-group $RG   --scale-rule-name ingest-queue --scale-rule-type azure-queue   --scale-rule-metadata "accountName=$STORAGE" "queueName=$QUEUE" "queueLength=1"   --scale-rule-auth "connection=queue-connection"
```

## 8. Try it

Send an email with an **invented** document attached to
`rolepay-agent@in.3lay.live` (the delivery goes to webhook.site, which is
public). Then:

```bash
# Executions: one should start within about 30 seconds of the email arriving.
az containerapp job execution list --name $JOB --resource-group $RG -o table

# Anything that couldn't be processed:
az storage message peek --queue-name $QUEUE-poison --account-name $STORAGE --auth-mode key
```

The run is recorded in the `events` table, and the envelope arrives on
webhook.site. The job's output (one line per message) is in the Container
Apps environment's Log Analytics workspace:

```
ContainerAppConsoleLogs_CL
| where ContainerJobName_s == "caj-3lay-orchy"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

## When an email isn't processed

Work along the path, from the job to the record:

1. **Did an execution start?**
   `az containerapp job execution list --name $JOB --resource-group $RG -o table`.
   "Succeeded" only means the job exited cleanly: a message moved to the
   poison queue counts as success.
2. **Is the message still waiting, or in the poison queue?**
   ```bash
   az storage queue metadata show --name $QUEUE --account-name $STORAGE --auth-mode key --query approximateMessageCount
   az storage message peek --queue-name $QUEUE-poison --account-name $STORAGE --auth-mode key --num-messages 5
   ```
   Poison messages are base64-encoded JSON; decode the `content` to read the
   `reason` and the original message:
   `echo "<content>" | base64 -d`
3. **What did the job log?** In the portal: the job `caj-3lay-orchy` →
   **Execution history** → an execution → **Console logs**, or the
   environment's Log Analytics workspace with the query in step 8. Each
   message logs one line: the event's status, why it was skipped, or why it
   went to the poison queue.
4. **Was the run recorded?** The `events` table (and `event_sources`,
   `event_steps`) for its `event_id`, which is the message's `ingest_id`.

To retry a poisoned message once the cause is fixed, put its original
message back on the ingest queue (base64-encoded, as the function does) and
remove it from the poison queue:

```bash
ORIGINAL='<the "message" value from the decoded poison message>'
az storage message put --queue-name $QUEUE --account-name $STORAGE --auth-mode key   --content "$(printf '%s' "$ORIGINAL" | base64 -w0)"
az storage message clear --queue-name $QUEUE-poison --account-name $STORAGE --auth-mode key
```
(`clear` empties the whole poison queue; to keep others, `az storage message get`
then `delete` just that one.)

## Updating the job

After a code change, build a new image (step 5), then point the job at it:

```bash
az containerapp job update --name $JOB --resource-group $RG \
  --image $ACR.azurecr.io/3lay-agents:<the new tag>
```

Using the dated tag rather than `latest` makes it clear which code each
execution ran, and lets you roll back by updating to an older tag.
