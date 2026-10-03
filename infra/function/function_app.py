"""3lay ingestion entry point.

POST /api/ingest accepts any body (in practice a raw email from the Cloudflare
email Worker) plus two headers: `X-3lay-Client` (who it was sent to) and
`X-3lay-Origin` (who sent it). It stores the raw bytes untouched in Blob
Storage under `<client>/<origin>/YYYY/MM/DD/<id>`, then enqueues a small job
message pointing at that blob. The raw
payload is the source of truth -- everything downstream (extraction,
webhooks) reads it from the blob rather than from the queue message, which
keeps queue messages tiny regardless of how large the input was.
"""

import hmac
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import azure.functions as func
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.storage.queue import QueueServiceClient, TextBase64EncodePolicy

# Keys in Azure App Configuration are namespaced per component, e.g.
# "FUNCTION:INGEST_QUEUE_NAME". The prefix is trimmed on load.
APP_CONFIG_PREFIX = "FUNCTION:"


def _load_settings() -> dict[str, str]:
    """All function config and secrets come from Azure App Configuration --
    APP_CONFIG_CONNECTION_STRING is the only app setting the function needs.
    Real environment variables still override individual values, and are
    the whole source when no connection string is set (offline dev)."""
    settings: dict[str, str] = {}

    conn_str = os.environ.get("APP_CONFIG_CONNECTION_STRING")
    if conn_str:
        from azure.appconfiguration.provider import SettingSelector, load
        from azure.identity import DefaultAzureCredential

        config = load(
            connection_string=conn_str,
            selects=[SettingSelector(key_filter=f"{APP_CONFIG_PREFIX}*")],
            trim_prefixes=[APP_CONFIG_PREFIX],
            keyvault_credential=DefaultAzureCredential(),
        )
        settings.update({key: str(value) for key, value in config.items()})

    settings.update(os.environ)
    return settings


SETTINGS = _load_settings()

RAW_CONTAINER = SETTINGS.get("RAW_CONTAINER_NAME", "raw-ingest")
INGEST_QUEUE = SETTINGS.get("INGEST_QUEUE_NAME", "ingest-jobs")

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _storage_clients() -> tuple[BlobServiceClient, QueueServiceClient]:
    """Connect with a connection string when one is set, otherwise use the
    Function App's managed identity against the named account."""
    conn_str = SETTINGS.get("INGEST_STORAGE_CONNECTION_STRING")
    if conn_str:
        return (
            BlobServiceClient.from_connection_string(conn_str),
            QueueServiceClient.from_connection_string(conn_str),
        )

    account = SETTINGS.get("INGEST_STORAGE_ACCOUNT_NAME")
    if not account:
        raise RuntimeError("Set INGEST_STORAGE_CONNECTION_STRING or INGEST_STORAGE_ACCOUNT_NAME")

    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    return (
        BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential),
        QueueServiceClient(f"https://{account}.queue.core.windows.net", credential=credential),
    )


@lru_cache
def _clients():
    """Built once per worker and reused across invocations. Creating the
    container/queue here is idempotent and means a fresh environment (e.g.
    Azurite) works without a separate provisioning step."""
    blob_service, queue_service = _storage_clients()

    container = blob_service.get_container_client(RAW_CONTAINER)
    try:
        container.create_container()
    except ResourceExistsError:
        pass

    # Base64-encode messages: that's what a queue-triggered Azure Function
    # expects by default, so the downstream worker can consume these as-is.
    queue = queue_service.get_queue_client(INGEST_QUEUE, message_encode_policy=TextBase64EncodePolicy())
    try:
        queue.create_queue()
    except ResourceExistsError:
        pass

    return container, queue


# Who the payload is for and who it came from, set by the Cloudflare email
# Worker: the client is the address the email was sent to, the origin is the
# sender. Only the Worker holds the function key and API key, so these
# headers can be trusted here. (Deliberately not the standard `Origin` header, which
# browsers set themselves for CORS.)
CLIENT_HEADER = "X-3lay-Client"
ORIGIN_HEADER = "X-3lay-Origin"

# Both values become blob path segments, so they're restricted to characters
# that can't add segments ("/") or climb out of a folder (".."). Covers email
# addresses (including "+" tags) as well as plain account ids. Max 254, the
# longest valid email address.
PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+@-]{0,253}$")


# A second credential on top of the Azure function key: a shared secret the
# Worker sends on every request, checked against FUNCTION:API_KEY in App
# Configuration. Unlike the function key, it's also enforced when running
# locally, and can be rotated without touching the Function App.
API_KEY_HEADER = "X-3lay-Api-Key"


def _json_response(body: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status_code, mimetype="application/json")


def _authorized(req: func.HttpRequest) -> bool:
    """Fails closed: with no API_KEY configured, every request is refused
    rather than the check being skipped."""
    expected = SETTINGS.get("API_KEY", "")
    if not expected:
        logging.error("API_KEY is not configured; refusing all requests")
        return False
    provided = req.headers.get(API_KEY_HEADER) or ""
    # Constant-time comparison, so response timing can't leak the key.
    return hmac.compare_digest(provided.encode(), expected.encode())


def _path_segment_header(req: func.HttpRequest, header: str) -> str:
    """Raises ValueError with a caller-facing message if the header is missing
    or unsafe to use as a path segment. Lowercased so that differently-cased
    spellings of the same address share one folder."""
    value = (req.headers.get(header) or "").strip().lower()
    if not value:
        raise ValueError(f"Missing {header} header")
    if not PATH_SEGMENT_PATTERN.match(value) or ".." in value:
        raise ValueError(
            f"{header} may only contain letters, digits, '@', '+', '-', '_' and '.' (max 254 characters)"
        )
    return value


@app.route(route="ingest", methods=["POST"])
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    if not _authorized(req):
        return _json_response({"error": "Unauthorized"}, 401)

    try:
        client = _path_segment_header(req, CLIENT_HEADER)
        origin = _path_segment_header(req, ORIGIN_HEADER)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    raw = req.get_body()
    if not raw:
        return _json_response({"error": "Request body is empty"}, 400)

    ingest_id = str(uuid.uuid4())
    received_at = datetime.now(timezone.utc)
    content_type = req.headers.get("content-type", "application/octet-stream")

    # Partitioned by client, then sender, then date, so each client's data
    # sits under its own prefix (easy to list, export or delete per client)
    # and stays easy to apply lifecycle rules to as volume grows. The body is
    # stored exactly as received.
    blob_name = f"{client}/{origin}/{received_at:%Y/%m/%d}/{ingest_id}"

    try:
        container, queue = _clients()

        container.upload_blob(
            name=blob_name,
            data=raw,
            content_settings=ContentSettings(content_type=content_type),
            metadata={
                "ingest_id": ingest_id,
                "client": client,
                "origin": origin,
                "received_at": received_at.isoformat(),
            },
        )

        queue.send_message(
            json.dumps(
                {
                    "ingest_id": ingest_id,
                    "client": client,
                    "origin": origin,
                    "container": RAW_CONTAINER,
                    "blob_name": blob_name,
                    "content_type": content_type,
                    "size_bytes": len(raw),
                    "received_at": received_at.isoformat(),
                }
            )
        )
    except Exception:
        # If the enqueue fails after the upload, the blob is left without a
        # job. That's preferable to the reverse (a job pointing at nothing),
        # and the caller gets a 500 so it knows to retry.
        logging.exception("Failed to ingest %s", ingest_id)
        return _json_response({"error": "Failed to store payload"}, 500)

    logging.info("Ingested %s for %s from %s (%d bytes)", ingest_id, client, origin, len(raw))
    return _json_response({"id": ingest_id, "status": "queued"}, 202)
