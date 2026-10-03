"""Alembic environment for the 3lay backend.

The database URL comes from the app's own settings (App Configuration's
BACKEND:DATABASE_URL, or DATABASE_URL), so migrations always run against the
same database the API uses. Everything lives in the `app` schema, including
Alembic's own version table.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app import models  # noqa: F401 -- registers the tables on Base.metadata
from app.config import get_settings
from app.database import DB_SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):
    # Only manage our own schema. Supabase keeps its own tables in `auth`,
    # `storage`, `public` etc.; autogenerate must never try to drop those.
    if type_ == "table":
        return object.schema == DB_SCHEMA
    return True


def run_migrations_offline() -> None:
    """Render the migration SQL without connecting (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=DB_SCHEMA,
        include_schemas=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        get_settings().database_url,
        poolclass=pool.NullPool,
        connect_args={"prepare_threshold": None},
    )
    with connectable.connect() as connection:
        # The version table lives in the app schema, so the schema has to
        # exist before Alembic looks for it.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=DB_SCHEMA,
            include_schemas=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
