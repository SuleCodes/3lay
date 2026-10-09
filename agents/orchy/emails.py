"""Reads the raw email (.eml) the ingest Function stores, and finds its attachments.

An email is a tree of MIME parts: a text body, maybe an HTML body, and the
attachments, each with a content type and a file name. Python's own email
package does the parsing.

Not handled yet: attachments inside a forwarded email (message/rfc822), and
the email's own text as a document.
"""

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path

# Mail clients often label attachments "application/octet-stream" ("some
# bytes"), so the file extension is used when the content type says nothing.
TYPES_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@dataclass
class Attachment:
    """One attachment: its position in the email, file name, content type and bytes."""

    index: int
    name: str
    content_type: str
    data: bytes


def content_type_of(part, name):
    content_type = part.get_content_type()
    if content_type == "application/octet-stream":
        return TYPES_BY_EXTENSION.get(Path(name).suffix.lower(), content_type)
    return content_type


def read_attachments(email_path):
    """Every attachment in the email, in order. Raises OSError if the file can't be read."""
    with open(email_path, "rb") as file:
        message = BytesParser(policy=policy.default).parse(file)
    attachments = []
    for index, part in enumerate(message.iter_attachments()):
        name = part.get_filename() or f"attachment-{index}"
        data = part.get_content()
        if isinstance(data, str):  # a text attachment, e.g. .txt or .csv
            data = data.encode("utf-8")
        elif not isinstance(data, bytes):  # e.g. an attached email
            data = part.as_bytes()
        attachments.append(Attachment(index, name, content_type_of(part, name), data))
    return attachments


def read_attachment(email_path, index):
    """One attachment's bytes. The email is read again rather than kept in the state."""
    return read_attachments(email_path)[index]
