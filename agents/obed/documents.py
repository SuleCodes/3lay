"""Turns documents into content blocks a model can read."""

import base64

import pymupdf


def pdf_to_image_blocks(path, dpi=200):
    """Renders each page of a PDF as a PNG, as LangChain standard image blocks.

    Higher dpi makes small text easier to read but costs more input tokens.
    """
    blocks = []
    with pymupdf.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("ascii")
            blocks.append({
                "type": "image",
                "base64": b64,
                "mime_type": "image/png",
            })
    return blocks
