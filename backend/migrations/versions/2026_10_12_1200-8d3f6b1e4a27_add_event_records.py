"""add event records: events, event_sources, event_steps, deliveries

Revision ID: 8d3f6b1e4a27
Revises: 5c1e7a9d2b40
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8d3f6b1e4a27'
down_revision: Union[str, Sequence[str], None] = '5c1e7a9d2b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"


def jsonb():
    return postgresql.JSONB(astext_type=sa.Text())


def event_child_key():
    return sa.ForeignKeyConstraint(["event_pk"], [f"{SCHEMA}.events.id"], ondelete="CASCADE")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("case_ref", sa.String(), nullable=False),
        sa.Column("sender", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("billable", sa.Boolean(), nullable=True),
        sa.Column("envelope", jsonb(), nullable=True),
        sa.Column("personal_data_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_events_user_id", "events", ["user_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_app_events_user_received", "events", ["user_id", "received_at"],
                    unique=False, schema=SCHEMA)
    # A queue message delivered twice is the same event_id + run: recognised and skipped.
    op.create_index("uq_events_event_id_run", "events", ["event_id", "run"], unique=True,
                    schema=SCHEMA)

    op.create_table(
        "event_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_pk", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("forwarded_from", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("checks", jsonb(), nullable=True),
        sa.Column("extraction", jsonb(), nullable=True),
        event_child_key(),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_event_sources_event_pk", "event_sources", ["event_pk"],
                    unique=False, schema=SCHEMA)
    op.create_index("uq_event_sources_event_position", "event_sources", ["event_pk", "position"],
                    unique=True, schema=SCHEMA)

    op.create_table(
        "event_steps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_pk", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(), nullable=False),
        sa.Column("source_position", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("details", jsonb(), nullable=True),
        event_child_key(),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_event_steps_event_pk", "event_steps", ["event_pk"],
                    unique=False, schema=SCHEMA)
    op.create_index("uq_event_steps_event_sequence", "event_steps", ["event_pk", "sequence"],
                    unique=True, schema=SCHEMA)

    op.create_table(
        "deliveries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_pk", sa.String(), nullable=False),
        sa.Column("destination_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("webhook_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_excerpt", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("retry_in_seconds", sa.Float(), nullable=True),
        event_child_key(),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_deliveries_event_pk", "deliveries", ["event_pk"],
                    unique=False, schema=SCHEMA)
    op.create_index("ix_app_deliveries_destination_id", "deliveries", ["destination_id"],
                    unique=False, schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_app_deliveries_destination_id", table_name="deliveries", schema=SCHEMA)
    op.drop_index("ix_app_deliveries_event_pk", table_name="deliveries", schema=SCHEMA)
    op.drop_table("deliveries", schema=SCHEMA)
    op.drop_index("uq_event_steps_event_sequence", table_name="event_steps", schema=SCHEMA)
    op.drop_index("ix_app_event_steps_event_pk", table_name="event_steps", schema=SCHEMA)
    op.drop_table("event_steps", schema=SCHEMA)
    op.drop_index("uq_event_sources_event_position", table_name="event_sources", schema=SCHEMA)
    op.drop_index("ix_app_event_sources_event_pk", table_name="event_sources", schema=SCHEMA)
    op.drop_table("event_sources", schema=SCHEMA)
    op.drop_index("uq_events_event_id_run", table_name="events", schema=SCHEMA)
    op.drop_index("ix_app_events_user_received", table_name="events", schema=SCHEMA)
    op.drop_index("ix_app_events_user_id", table_name="events", schema=SCHEMA)
    op.drop_table("events", schema=SCHEMA)
