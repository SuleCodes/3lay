"""add trace_id to events: each run's correlation id (its trace in Datadog)

Revision ID: 6e2a4c8d1f93
Revises: 3b7d9e2f5a61
Create Date: 2026-10-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e2a4c8d1f93'
down_revision: Union[str, Sequence[str], None] = '3b7d9e2f5a61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("events", sa.Column("trace_id", sa.String(), nullable=True), schema=SCHEMA)
    op.create_index("ix_app_events_trace_id", "events", ["trace_id"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_app_events_trace_id", table_name="events", schema=SCHEMA)
    op.drop_column("events", "trace_id", schema=SCHEMA)
