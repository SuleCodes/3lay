"""Shared test helpers. pytest loads this file automatically before the tests.

Tests never call a real model, send traces or touch the real database: the
model call is faked, and LangSmith tracing and the database URLs are switched
off here even if agents/.env (which VS Code loads for tests) sets them.
"""

import os
from email.message import EmailMessage
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from orchy import nodes
from store.db import metadata, users
from store.secret_box import generate_key

os.environ["LANGSMITH_TRACING"] = "false"
for _name in ("DATABASE_URL", "ADMIN_DATABASE_URL", "APP_CONFIG_CONNECTION_STRING"):
    os.environ.pop(_name, None)

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


def email_message(*attachments, sender="actor@example.com",
                  body="Please find my statement attached."):
    """An email with attachments. Each is (file name, content type, bytes), or an
    EmailMessage, which is attached whole, like "Forward as attachment" does."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "client@in.example.com"
    message["Subject"] = "My statement"
    message.set_content(body)
    for attachment in attachments:
        if isinstance(attachment, EmailMessage):
            message.add_attachment(attachment)
            continue
        name, content_type, data = attachment
        maintype, subtype = content_type.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return message


def write_email(path, *attachments, body="Please find my statement attached."):
    """Writes a .eml like the one the ingest Function stores (see email_message)."""
    path.write_bytes(email_message(*attachments, body=body).as_bytes())
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


ENCRYPTION_KEY = generate_key()


@pytest.fixture
def database(monkeypatch):
    """An in-memory SQLite database with the pipeline's tables and two clients.

    SQLite stands in for Postgres: the "app" schema is an attached database,
    and the tables come from store/db.py (the real ones from the backend's
    migrations). Also sets an encryption key for destination secrets.
    """
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach_app_schema(connection, _record):
        connection.execute("ATTACH DATABASE ':memory:' AS app")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys = ON")

    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(users.insert(), [
            {"id": "u1", "email": "ops@rolepay.example", "username": "rolepay-agent"},
            {"id": "u2", "email": "ops@other.example", "username": "other-client"},
        ])
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY_VERSION", "1")
    return engine
