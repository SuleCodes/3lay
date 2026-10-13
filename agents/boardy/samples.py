"""The client's sample documents: what Boardy reads, and tries the draft on.

Samples are listed in Boardy's state as {"id", "name", "content_type", "path"}
and tools refer to them by id only. The model never passes a file path, so it
can only reach documents the client provided, never any other file.

In the terminal they come from a folder (--samples); in the backend (stage B7)
they'll be uploads. They're real client documents: personal data, kept no
longer than events are.
"""

from pathlib import Path

from obed.documents import SUPPORTED_TYPES, page_count, to_image_blocks

MAX_SAMPLES = 5
MAX_SAMPLE_BYTES = 10 * 1024 * 1024
CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png"}


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


def find_sample(samples, sample_id):
    return next((s for s in samples if s["id"] == sample_id), None)


def sample_inputs(sample):
    """The sample's pages as image blocks, the same way Orchy sends attachments to Obed."""
    if sample["content_type"] not in SUPPORTED_TYPES:
        raise ValueError(f"{sample['content_type']} isn't a supported document type")
    return to_image_blocks(Path(sample["path"]).read_bytes(), sample["content_type"])


def samples_text(samples):
    """The samples, as listed in Boardy's instructions."""
    if not samples:
        return "none (the client hasn't provided any)"
    return ", ".join(f"{s['id']} ({s['name']})" for s in samples)
