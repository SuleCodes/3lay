"""Turns documents into content blocks a model can read.

Works from bytes, because documents arrive as email attachments (and later
from Blob Storage), never as files on disk.
"""

import base64

import pymupdf

# Document types Obed can read, and pymupdf's name for each. Photos matter:
# actors often photograph a payslip rather than forwarding a PDF.
SUPPORTED_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpeg",
    "image/png": "png",
}


def page_count(data, content_type):
    """How many pages the document has (1 for a photo). Raises if it won't open."""
    with pymupdf.open(stream=data, filetype=SUPPORTED_TYPES[content_type]) as doc:
        return doc.page_count


def image_block(png_or_jpeg_bytes, mime_type):
    """A LangChain standard image block."""
    return {
        "type": "image",
        "base64": base64.b64encode(png_or_jpeg_bytes).decode("ascii"),
        "mime_type": mime_type,
    }


def to_image_blocks(data, content_type, dpi=200):
    """One image block per page: PDF pages are rendered, photos are sent as they are.

    Higher dpi makes small text easier to read but costs more input tokens.
    """
    if content_type.startswith("image/"):
        return [image_block(data, content_type)]
    blocks = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            blocks.append(image_block(page.get_pixmap(dpi=dpi).tobytes("png"), "image/png"))
    return blocks
