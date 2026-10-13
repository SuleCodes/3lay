"""Connection and table definitions for the tables the pipeline uses.

The tables themselves are created by the backend's migrations; these
definitions only describe them for queries (and let the tests build the same
tables in SQLite). Keep them in step with backend/app/models.py.
"""

import os
from functools import cache

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
                        MetaData, String, Table, create_engine, text)

SCHEMA = "app"  # 3lay's tables live outside Supabase's REST-exposed `public` schema
metadata = MetaData(schema=SCHEMA)

users = Table(
    "users", metadata,
    Column("id", String, primary_key=True),
    Column("email", String, nullable=False),
    Column("username", String, unique=True),
    Column("created_at", DateTime(timezone=True)),
)

ONLY_ACTIVE = "status = 'active'"
client_configurations = Table(
    "client_configurations", metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("config", JSON, nullable=False),  # JSONB in Postgres
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True)),
    Index("uq_client_configurations_user_version", "user_id", "version", unique=True),
    Index("uq_client_configurations_one_active", "user_id", unique=True,
          postgresql_where=text(ONLY_ACTIVE), sqlite_where=text(ONLY_ACTIVE)),
)

destination_secrets = Table(
    "destination_secrets", metadata,
    Column("destination_id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("ciphertext", String, nullable=False),
    Column("previous_ciphertext", String),
    Column("previous_expires_at", DateTime(timezone=True)),
    Column("prefix", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("rotated_at", DateTime(timezone=True)),
)


events = Table(
    "events", metadata,
    Column("id", String, primary_key=True),
    Column("event_id", String, nullable=False),
    Column("run", Integer, nullable=False),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("case_ref", String, nullable=False),
    Column("sender", String),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False),
    Column("error_code", String),
    Column("error_message", String),
    Column("config_version", Integer),
    Column("model", String),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("billable", Boolean),
    Column("envelope", JSON),  # JSONB in Postgres
    Column("personal_data_expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("uq_events_event_id_run", "event_id", "run", unique=True),
)

event_sources = Table(
    "event_sources", metadata,
    Column("id", String, primary_key=True),
    Column("event_pk", String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("name", String, nullable=False),
    Column("content_type", String, nullable=False),
    Column("location", String),
    Column("forwarded_from", String),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("verdict", String),
    Column("error_code", String),
    Column("error_message", String),
    Column("checks", JSON),
    Column("extraction", JSON),
    Index("uq_event_sources_event_position", "event_pk", "position", unique=True),
)

event_steps = Table(
    "event_steps", metadata,
    Column("id", String, primary_key=True),
    Column("event_pk", String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("node", String, nullable=False),
    Column("source_position", Integer),
    Column("outcome", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    Column("error", String),
    Column("details", JSON),
    Index("uq_event_steps_event_sequence", "event_pk", "sequence", unique=True),
)

deliveries = Table(
    "deliveries", metadata,
    Column("id", String, primary_key=True),
    Column("event_pk", String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
    Column("destination_id", String, nullable=False),
    Column("url", String, nullable=False),
    Column("webhook_id", String, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("http_status", Integer),
    Column("response_excerpt", String),
    Column("error", String),
    Column("attempted_at", DateTime(timezone=True), nullable=False),
    Column("duration_ms", Integer),
    Column("retry_in_seconds", Float),
)


def psycopg_url(url):
    """Point a plain postgresql:// URL (as Supabase gives it) at the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url):
    return create_engine(
        psycopg_url(url),
        pool_pre_ping=True,  # poolers close idle connections
        # Server-side prepared statements break behind Supabase's transaction pooler.
        connect_args={"prepare_threshold": None} if url.startswith("postgres") else {},
    )


def database_configured():
    return bool(os.environ.get("DATABASE_URL"))


@cache
def engine():
    """The pipeline's database connection: DATABASE_URL, from App Configuration's
    AGENTS:DATABASE_URL. It connects as agents_pipeline, a role limited to the
    tables the pipeline needs, not the backend's owner role."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL isn't set (App Configuration: AGENTS:DATABASE_URL)")
    return make_engine(url)
