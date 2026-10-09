"""Shared test helpers. pytest loads this file automatically before the tests.

Tests never call a real model or send traces: the model call is faked, and
LangSmith tracing is switched off here even if agents/.env turns it on.
"""

import os
from email.message import EmailMessage
from types import SimpleNamespace

import httpx
import pymupdf
import pytest

from orchy import nodes

os.environ["LANGSMITH_TRACING"] = "false"

USAGE = {"input_tokens": 3000, "output_tokens": 1000, "total_tokens": 4000}


def pdf_bytes(pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def png_bytes():
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    return pixmap.tobytes("png")


def write_email(path, *attachments, body="Please find my statement attached."):
    """Writes a .eml like the one the ingest Function stores.

    Each attachment is (file name, content type, bytes).
    """
    message = EmailMessage()
    message["From"] = "actor@example.com"
    message["To"] = "client@in.example.com"
    message["Subject"] = "My statement"
    message.set_content(body)
    for name, content_type, data in attachments:
        maintype, subtype = content_type.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    path.write_bytes(message.as_bytes())
    return str(path)


def api_error(error_class, status):
    request = httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions")
    return error_class("simulated", response=httpx.Response(status, request=request), body=None)


@pytest.fixture
def fake_model(monkeypatch):
    """Replaces Obed's model. Call it with the answers to give, one per model call.

    Each answer is a dict to return as the extraction, or an exception to raise.
    The last answer repeats if there are more calls than answers.
    """
    monkeypatch.setattr(nodes, "obed_model", lambda: (object(), "test/model:provider"))
    calls = []

    def set_answers(*answers):
        remaining = list(answers)

        def obed_extract(*args):
            calls.append(args)
            answer = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(answer, Exception):
                raise answer
            return {"parsed": answer, "parsing_error": None,
                    "raw": SimpleNamespace(usage_metadata=USAGE)}

        monkeypatch.setattr(nodes, "obed_extract", obed_extract)
        return calls

    return set_answers
