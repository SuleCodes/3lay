"""Creates the tables Boardy's saved conversations live in, and lets Boardy's role use them.

    python scripts/setup_conversations.py --generate-key   (a new CONVERSATION_ENCRYPTION_KEY)
    python scripts/setup_conversations.py                  (create or upgrade the tables)

The tables are LangGraph's own (checkpoints, checkpoint_blobs,
checkpoint_writes, checkpoint_migrations), created by its setup(), in their
own schema, "boardy", rather than with the backend's Alembic migrations: they
follow LangGraph's versions, and setup() upgrades them when LangGraph changes
them. Run it again after upgrading langgraph-checkpoint-postgres.

agents_onboarding (Boardy's role) can't create tables, so this uses the
backend's connection, like save_configuration.py: App Configuration's
BACKEND:DATABASE_URL (or ADMIN_DATABASE_URL in agents/.env). Then it grants
agents_onboarding what the checkpointer needs on those tables, and nothing on
any other schema.
"""

import argparse
import os
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
import psycopg  # noqa: E402
from langgraph.checkpoint.postgres import PostgresSaver  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from boardy.conversations import CHECKPOINT_SCHEMA, plain_postgres_url  # noqa: E402
from settings import load_settings  # noqa: E402
from store.secret_box import generate_key  # noqa: E402

ROLE = "agents_onboarding"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--generate-key", action="store_true",
                        help="print a new key for CONVERSATION_ENCRYPTION_KEY and exit")
    args = parser.parse_args()
    if args.generate_key:
        print(generate_key())
        print("Put it in Key Vault as the value of AGENTS:CONVERSATION_ENCRYPTION_KEY.")
        return

    load_settings(("BACKEND:DATABASE_URL", "ADMIN_DATABASE_URL"))
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        sys.exit("No admin database connection: set APP_CONFIG_CONNECTION_STRING (to read "
                 "BACKEND:DATABASE_URL) or ADMIN_DATABASE_URL in agents/.env.")

    with psycopg.connect(plain_postgres_url(url), autocommit=True, row_factory=dict_row,
                         prepare_threshold=None) as connection:
        connection.execute(f"create schema if not exists {CHECKPOINT_SCHEMA}")
        connection.execute(f"set search_path to {CHECKPOINT_SCHEMA}")
        PostgresSaver(connection).setup()
        connection.execute(f"grant usage on schema {CHECKPOINT_SCHEMA} to {ROLE}")
        connection.execute(f"grant select, insert, update, delete on all tables in schema "
                           f"{CHECKPOINT_SCHEMA} to {ROLE}")
        tables = [row["table_name"] for row in connection.execute(
            "select table_name from information_schema.tables where table_schema = %s "
            "order by table_name", (CHECKPOINT_SCHEMA,))]
    print(f"Schema {CHECKPOINT_SCHEMA}: {', '.join(tables)}; {ROLE} can use them.")


if __name__ == "__main__":
    main()
