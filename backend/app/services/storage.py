"""Access to the ingest storage account (raw emails written by the ingest
Azure Function), used to delete a client's stored data with their account."""

import logging

from app.config import get_settings

logger = logging.getLogger("3lay.storage")
settings = get_settings()

# Azure's batch delete accepts at most 256 blobs per request.
_BATCH_SIZE = 256


class StorageNotConfiguredError(Exception):
    pass


def _container_client():
    from azure.storage.blob import BlobServiceClient

    if settings.ingest_storage_connection_string:
        service = BlobServiceClient.from_connection_string(settings.ingest_storage_connection_string)
    elif settings.ingest_storage_account_name:
        from azure.identity import DefaultAzureCredential

        service = BlobServiceClient(
            f"https://{settings.ingest_storage_account_name}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
    else:
        raise StorageNotConfiguredError(
            "Set BACKEND:INGEST_STORAGE_CONNECTION_STRING or BACKEND:INGEST_STORAGE_ACCOUNT_NAME"
        )
    return service.get_container_client(settings.raw_container_name)


def delete_client_blobs(forwarding_address: str) -> int:
    """Deletes every blob under `<forwarding_address>/` -- everything the
    ingest function stored for this client -- and returns how many were
    deleted. Raises on failure, so the caller can stop before deleting the
    account and the user can retry."""
    from azure.core.exceptions import ResourceNotFoundError

    container = _container_client()
    # The trailing slash matters: without it, "rolepay@..." would also match
    # a different client whose address merely starts with the same text.
    prefix = f"{forwarding_address}/"

    deleted = 0
    batch: list[str] = []
    try:
        for blob in container.list_blobs(name_starts_with=prefix):
            batch.append(blob.name)
            if len(batch) == _BATCH_SIZE:
                container.delete_blobs(*batch, delete_snapshots="include")
                deleted += len(batch)
                batch = []
        if batch:
            container.delete_blobs(*batch, delete_snapshots="include")
            deleted += len(batch)
    except ResourceNotFoundError:
        # The container doesn't exist yet: nothing was ever stored.
        logger.info("Container %s not found; nothing to delete for %s", settings.raw_container_name, prefix)

    logger.info("Deleted %d stored blob(s) under %s", deleted, prefix)
    return deleted
