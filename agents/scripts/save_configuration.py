"""Saves a client's configuration to the database as its next version, and activates it.

    python scripts/save_configuration.py rolepay-agent
    python scripts/save_configuration.py --generate-key       (a new encryption key)

Builds the configuration from the local files that have stood in for it so far:
tmp/schema/..., tmp/rules/... and tmp/destinations.json, plus the instructions
below. It's checked as a whole before saving (the same check the dashboard
and Boardy will use), saved as the client's next version and activated, which
retires the previous version.

Each destination without a signing secret in the database gets one: generated,
stored encrypted (SECRETS_ENCRYPTION_KEY) and printed once, as the client
would see it. Existing secrets are kept, so saving a new version never breaks
a client's verification.

Writes need more than the pipeline's limited database role, so this uses the
backend's connection: App Configuration's BACKEND:DATABASE_URL (or
ADMIN_DATABASE_URL in agents/.env, which overrides it locally).
"""

import argparse
import json
import os
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from settings import load_settings  # noqa: E402
from store.configurations import load_active_configuration, save_configuration  # noqa: E402
from store.db import make_engine  # noqa: E402
from store.secret_box import generate_key  # noqa: E402
from store.secrets import create_or_rotate_secret, has_secret  # noqa: E402

SCHEMA_PATH = AGENTS_DIR / "tmp" / "schema" / "rolepay-income-statement.v1.json"
RULES_PATH = AGENTS_DIR / "tmp" / "rules" / "rolepay-income-statement.v1.json"
DESTINATIONS_PATH = AGENTS_DIR / "tmp" / "destinations.json"
DOCUMENT_TYPE = "income_statement"
INSTRUCTIONS = "Extract the document into the output schema."
MAX_ATTEMPTS = 3


def read_json(path, default=None):
    if not path.exists():
        if default is not None:
            return default
        sys.exit(f"Missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def configuration_from_files():
    return {
        "processing": {"max_attempts": MAX_ATTEMPTS},
        "document_types": [{
            "name": DOCUMENT_TYPE,
            "instructions": INSTRUCTIONS,
            "schema": read_json(SCHEMA_PATH),
            "rules": read_json(RULES_PATH, default=[]),
        }],
        "analyses": [],
        "destinations": read_json(DESTINATIONS_PATH, default=[]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", nargs="?", help="the client, e.g. rolepay-agent")
    parser.add_argument("--generate-key", action="store_true",
                        help="print a new encryption key for SECRETS_ENCRYPTION_KEY and exit")
    args = parser.parse_args()

    if args.generate_key:
        print(generate_key())
        print("Put it in Key Vault (and agents/.env for local runs) as SECRETS_ENCRYPTION_KEY.")
        return
    if not args.username:
        parser.error("give the client's username, or --generate-key")

    load_settings(("BACKEND:DATABASE_URL", "ADMIN_DATABASE_URL"))
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        sys.exit("No admin database connection: set APP_CONFIG_CONNECTION_STRING (to read "
                 "BACKEND:DATABASE_URL) or ADMIN_DATABASE_URL in agents/.env.")
    engine = make_engine(url)

    config = configuration_from_files()
    try:
        version = save_configuration(engine, args.username, config)
    except ValueError as exc:  # the configuration didn't pass its check
        sys.exit(str(exc))
    print(f"Saved and activated version {version} for {args.username}.")

    _, _, user_id = load_active_configuration(engine, args.username)
    for destination in config["destinations"]:
        if has_secret(engine, destination["id"]):
            print(f"  {destination['id']}: kept its existing signing secret")
            continue
        secret = create_or_rotate_secret(engine, user_id, destination["id"])
        print(f"  {destination['id']}: new signing secret (shown once, as the client sees it):")
        print(f"    {secret}")


if __name__ == "__main__":
    main()
