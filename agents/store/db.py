"""Connection and table definitions for the tables the pipeline uses.

The tables themselves are created by the backend's migrations; these
definitions only describe them for queries (and let the tests build the same
tables in SQLite). Keep them in step with backend/app/models.py.
"""

import os
from functools import cache

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Index, Integer, MetaData, String,
                        Table, create_engine, text)

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
