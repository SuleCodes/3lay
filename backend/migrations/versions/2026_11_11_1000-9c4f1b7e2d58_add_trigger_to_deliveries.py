"""add trigger to deliveries: "automatic" (the pipeline) or "manual" (Resend in the dashboard)

Revision ID: 9c4f1b7e2d58
Revises: 6e2a4c8d1f93
Create Date: 2026-11-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4f1b7e2d58'
down_revision: Union[str, Sequence[str], None] = '6e2a4c8d1f93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("deliveries", sa.Column("trigger", sa.String(), nullable=False,
                                          server_default="automatic"), schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("deliveries", "trigger", schema=SCHEMA)
