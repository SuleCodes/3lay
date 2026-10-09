"""Reads the raw email (.eml) the ingest Function stores, and finds its attachments.

An email is a tree of MIME parts: a text body, maybe an HTML body, and the
attachments, each with a content type and a file name. Python's own email
package does the parsing.

Forwarded emails: a normal "Forward" copies the attachments into the new email,
so they're ordinary attachments. "Forward as attachment" (Outlook, Apple Mail)
attaches the whole original email (message/rfc822) instead, with the documents
inside it; those are looked for inside, down to MAX_DEPTH levels of forwarding.

Not handled yet: the email's own text as a document.
"""

from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
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

# How many emails-inside-emails to open. Real forwards rarely go past 2 or 3;
# the limit stops a deliberately deep email from being opened forever.
MAX_DEPTH = 5


@dataclass
class Attachment:
    """One attachment: where it is in the email, what it is, and its bytes.

    index is its position in the flattened list (attachments of forwarded
    emails included, in the order they appear), which is how a document is
    found again when the email is re-read. location shows where it sits, e.g.
    "1 > 0" is the first attachment of the email attached second.
    forwarded_from is the sender of the forwarded email it came from, if any.
    """

    index: int
    name: str
    content_type: str
    data: bytes
    location: str
    forwarded_from: str | None = None


def content_type_of(part, name):
    content_type = part.get_content_type()
    if content_type == "application/octet-stream":
        return TYPES_BY_EXTENSION.get(Path(name).suffix.lower(), content_type)
    return content_type


def _walk(message, depth, location, forwarded_from, found):
    """Adds the message's attachments to found, opening attached emails as it goes."""
    for position, part in enumerate(message.iter_attachments()):
        here = f"{location} > {position}" if location else str(position)
        content = part.get_content()

        if isinstance(content, EmailMessage) and depth < MAX_DEPTH:
            # A forwarded email: its own attachments are the documents.
            _walk(content, depth + 1, here, content.get("From") or forwarded_from, found)
            continue

        name = part.get_filename() or f"attachment-{len(found)}"
        if isinstance(content, str):  # a text attachment, e.g. .txt or .csv
            data = content.encode("utf-8")
        elif isinstance(content, bytes):
            data = content
        else:  # an attached email past MAX_DEPTH, kept as it is (unsupported)
            data = part.as_bytes()
        found.append(Attachment(len(found), name, content_type_of(part, name), data,
                                here, forwarded_from))


def read_attachments(email_path):
    """Every attachment in the email, forwarded emails' included, in order.

    Raises OSError if the file can't be read.
    """
    with open(email_path, "rb") as file:
        message = BytesParser(policy=policy.default).parse(file)
    found = []
    _walk(message, 0, "", None, found)
    return found


def read_sender(email_path):
    """The From address, lower-cased, e.g. "ap@acme.com" from '"Acme" <AP@acme.com>'.

    Later this comes from the Worker's X-3lay-Origin header instead, which has
    already done the same with the envelope sender as a fallback.
    """
    with open(email_path, "rb") as file:
        message = BytesParser(policy=policy.default).parse(file, headersonly=True)
    sender = message["From"]
    if sender is None or not sender.addresses:
        return None
    return sender.addresses[0].addr_spec.lower() or None


def read_attachment(email_path, index):
    """One attachment's bytes. The email is read again rather than kept in the state."""
    return read_attachments(email_path)[index]
