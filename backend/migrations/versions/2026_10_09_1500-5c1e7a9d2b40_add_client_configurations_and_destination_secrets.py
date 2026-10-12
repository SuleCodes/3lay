"""add client_configurations and destination_secrets

Revision ID: 5c1e7a9d2b40
Revises: de04f183178e
Create Date: 2026-10-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '5c1e7a9d2b40'
down_revision: Union[str, Sequence[str], None] = 'de04f183178e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"
ONLY_ACTIVE = sa.text("status = 'active'")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "client_configurations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_client_configurations_user_id", "client_configurations",
                    ["user_id"], unique=False, schema=SCHEMA)
    op.create_index("uq_client_configurations_user_version", "client_configurations",
                    ["user_id", "version"], unique=True, schema=SCHEMA)
    # At most one active version per client, enforced by the database itself.
    op.create_index("uq_client_configurations_one_active", "client_configurations",
                    ["user_id"], unique=True, schema=SCHEMA, postgresql_where=ONLY_ACTIVE)

    op.create_table(
        "destination_secrets",
        sa.Column("destination_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.String(), nullable=False),
        sa.Column("previous_ciphertext", sa.String(), nullable=True),
        sa.Column("previous_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("destination_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_destination_secrets_user_id", "destination_secrets",
                    ["user_id"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_app_destination_secrets_user_id", table_name="destination_secrets",
                  schema=SCHEMA)
    op.drop_table("destination_secrets", schema=SCHEMA)
    op.drop_index("uq_client_configurations_one_active", table_name="client_configurations",
                  schema=SCHEMA, postgresql_where=ONLY_ACTIVE)
    op.drop_index("uq_client_configurations_user_version", table_name="client_configurations",
                  schema=SCHEMA)
    op.drop_index("ix_app_client_configurations_user_id", table_name="client_configurations",
                  schema=SCHEMA)
    op.drop_table("client_configurations", schema=SCHEMA)
