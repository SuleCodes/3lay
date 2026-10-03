"""create users, api_keys and magic_link_tokens

Revision ID: 816df821bd9b
Revises: 
Create Date: 2026-10-03 12:02:19.331018

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '816df821bd9b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "app"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_users_email", "users", ["email"], unique=True, schema=SCHEMA)

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_api_keys_user_id", "api_keys", ["user_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_app_api_keys_key_hash", "api_keys", ["key_hash"], unique=True, schema=SCHEMA)

    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_app_magic_link_tokens_email", "magic_link_tokens", ["email"], unique=False, schema=SCHEMA)
    op.create_index(
        "ix_app_magic_link_tokens_token_hash", "magic_link_tokens", ["token_hash"], unique=True, schema=SCHEMA
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_app_magic_link_tokens_token_hash", table_name="magic_link_tokens", schema=SCHEMA)
    op.drop_index("ix_app_magic_link_tokens_email", table_name="magic_link_tokens", schema=SCHEMA)
    op.drop_table("magic_link_tokens", schema=SCHEMA)
    op.drop_index("ix_app_api_keys_key_hash", table_name="api_keys", schema=SCHEMA)
    op.drop_index("ix_app_api_keys_user_id", table_name="api_keys", schema=SCHEMA)
    op.drop_table("api_keys", schema=SCHEMA)
    op.drop_index("ix_app_users_email", table_name="users", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
    # The schema itself is left in place: it also holds Alembic's version table.
