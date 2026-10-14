"""The client's sample documents: what Boardy reads, and tries the draft on.

Samples are listed in Boardy's state as {"id", "name", "content_type"} plus
where the file is: "path" (a local file, from the terminal's --samples folder)
or "blob" (an upload in the chat, in Blob Storage). Tools refer to them by id
only. The model never passes a file path, so it can only reach documents the
client provided, never any other file.

Uploads are stored under "<customer id>/<conversation id>/" in the samples
container (AGENTS:SAMPLES_CONTAINER_NAME, default onboarding-samples) of the
ingest storage account (AGENTS:INGEST_STORAGE_ACCOUNT_NAME). They're real
client documents: personal data, kept no longer than events are.
"""

import os
from functools import cache
from pathlib import Path

from obed.documents import SUPPORTED_TYPES, page_count, to_image_blocks

MAX_SAMPLES = 5
MAX_SAMPLE_BYTES = 10 * 1024 * 1024
CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png"}
EXTENSIONS = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}
DEFAULT_SAMPLES_CONTAINER = "onboarding-samples"


def samples_from_folder(folder):
    """The supported documents in a folder, as samples. Raises ValueError listing any problems."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"{folder} isn't a folder")
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in CONTENT_TYPES)
    if not files:
        raise ValueError(f"{folder} has no PDF, JPEG or PNG files")
    if len(files) > MAX_SAMPLES:
        raise ValueError(f"{folder} has {len(files)} documents; the limit is {MAX_SAMPLES}")

    samples, problems = [], []
    for number, path in enumerate(files, start=1):
        content_type = CONTENT_TYPES[path.suffix.lower()]
        if path.stat().st_size > MAX_SAMPLE_BYTES:
            problems.append(f"{path.name}: larger than {MAX_SAMPLE_BYTES // (1024 * 1024)} MB")
            continue
        try:
            pages = page_count(path.read_bytes(), content_type)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            problems.append(f"{path.name}: couldn't open it ({exc})")
            continue
        if pages == 0:
            problems.append(f"{path.name}: it has no pages")
            continue
        samples.append({"id": f"sample_{number}", "name": path.name,
                        "content_type": content_type, "path": str(path.resolve())})
    if problems:
        raise ValueError("Some samples can't be used:\n- " + "\n- ".join(problems))
    return samples


def document_problem(data, content_type):
    """Why these bytes can't be a sample, or None."""
    if content_type not in SUPPORTED_TYPES:
        return "only PDF, JPEG and PNG documents can be used"
    if len(data) > MAX_SAMPLE_BYTES:
        return f"it's larger than {MAX_SAMPLE_BYTES // (1024 * 1024)} MB"
    try:
        pages = page_count(data, content_type)
    except Exception:  # pylint: disable=broad-exception-caught
        return "it couldn't be opened"
    return "it has no pages" if pages == 0 else None


@cache
def samples_container():
    """The Blob Storage container for uploaded samples."""
    # Imported here so the terminal and the tests don't need the Azure packages loaded.
    from azure.identity import DefaultAzureCredential  # pylint: disable=import-outside-toplevel
    from azure.storage.blob import BlobServiceClient  # pylint: disable=import-outside-toplevel
    account = os.environ["INGEST_STORAGE_ACCOUNT_NAME"]
    service = BlobServiceClient(f"https://{account}.blob.core.windows.net",
                                credential=DefaultAzureCredential())
    return service.get_container_client(
        os.environ.get("SAMPLES_CONTAINER_NAME", DEFAULT_SAMPLES_CONTAINER))


def store_sample(samples, user_id, conversation_id, name, content_type, data,
                 container=None):
    """Checks an uploaded document and stores it. Returns the new sample.

    samples are the conversation's samples so far. Raises ValueError, in words
    for the client, if it can't be used. The blob's name is made from ids, never
    from the file's name, which is kept only as a label.
    """
    if len(samples) >= MAX_SAMPLES:
        raise ValueError(f"You can add up to {MAX_SAMPLES} samples.")
    problem = document_problem(data, content_type)
    if problem:
        raise ValueError(f"{name} can't be used: {problem}.")
    sample_id = f"sample_{len(samples) + 1}"
    blob = f"{user_id}/{conversation_id}/{sample_id}{EXTENSIONS[content_type]}"
    (container or samples_container()).upload_blob(blob, data, overwrite=True)
    return {"id": sample_id, "name": Path(name).name[:200] or sample_id,
            "content_type": content_type, "blob": blob}


def sample_bytes(sample, container=None):
    """The sample's file: from disk (terminal) or Blob Storage (uploads)."""
    if "path" in sample:
        return Path(sample["path"]).read_bytes()
    return (container or samples_container()).download_blob(sample["blob"]).readall()


def find_sample(samples, sample_id):
    return next((s for s in samples if s["id"] == sample_id), None)


def sample_inputs(sample):
    """The sample's pages as image blocks, the same way Orchy sends attachments to Obed."""
    if sample["content_type"] not in SUPPORTED_TYPES:
        raise ValueError(f"{sample['content_type']} isn't a supported document type")
    return to_image_blocks(sample_bytes(sample), sample["content_type"])


def samples_text(samples):
    """The samples, as listed in Boardy's instructions."""
    if not samples:
        return "none (the client hasn't provided any)"
    return ", ".join(f"{s['id']} ({s['name']})" for s in samples)
