"""Reviews and activates a client's draft configuration, by hand.

Clients normally switch their configuration on themselves, by approving it in
Boardy's chat. This is for 3lay: to look at a draft, or to activate one if
switching on failed, or one saved from the terminal (talk_to_boardy.py).

    python scripts/activate_configuration.py rolepay-agent             (show the newest draft)
    python scripts/activate_configuration.py rolepay-agent --activate  (then activate it)
    python scripts/activate_configuration.py rolepay-agent --version 3 --activate

Without --activate it only shows the draft, so you can review it first. With
it, the draft becomes the client's active configuration (the previously active
one is retired), and each destination without a signing secret gets one:
generated, stored encrypted and printed once, to pass to the client. The
secret is created here, never by Boardy, so it's never sent to a model or
kept in a conversation.

Uses the backend's connection, like save_configuration.py: App Configuration's
BACKEND:DATABASE_URL (or ADMIN_DATABASE_URL in agents/.env).
"""

import argparse
import json
import os
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from sqlalchemy import select  # noqa: E402

from settings import load_settings  # noqa: E402
from store.activation import switch_on  # noqa: E402
from store.configurations import (ConfigurationNotFound, check_configuration,  # noqa: E402
                                  latest_draft, user_id_for)
from store.db import client_configurations, make_engine  # noqa: E402


def draft_version(engine, user_id, version):
    """(version, configuration) of the given draft, or the newest one."""
    if version is None:
        return latest_draft(engine, user_id)
    with engine.connect() as connection:
        row = connection.execute(
            select(client_configurations.c.config, client_configurations.c.status)
            .where(client_configurations.c.user_id == user_id,
                   client_configurations.c.version == version)).first()
    if row is None:
        return None
    if row.status != "draft":
        sys.exit(f"Version {version} is {row.status}, not a draft.")
    return version, row.config


def summary(config):
    document_type = config["document_types"][0]
    fields = list((document_type["schema"].get("properties") or {}))
    lines = [f"  document type: {document_type['name']}: {document_type.get('description', '')}",
             f"  instructions:  {document_type['instructions']}",
             f"  fields:        {', '.join(fields)}",
             f"  rules:         {', '.join(r['id'] for r in document_type.get('rules', []))}",
             f"  attempts:      {config['processing'].get('max_attempts')}"]
    lines += [f"  destination:   {d['id']} -> {d['url']} ({', '.join(d['events'])})"
              for d in config["destinations"]]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", help="the client, e.g. rolepay-agent")
    parser.add_argument("--version", type=int, help="the draft to use (default: the newest)")
    parser.add_argument("--activate", action="store_true", help="activate it after showing it")
    parser.add_argument("--json", action="store_true", help="show the whole configuration")
    args = parser.parse_args()

    load_settings(("BACKEND:DATABASE_URL", "ADMIN_DATABASE_URL"))
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        sys.exit("No admin database connection: set APP_CONFIG_CONNECTION_STRING (to read "
                 "BACKEND:DATABASE_URL) or ADMIN_DATABASE_URL in agents/.env.")
    engine = make_engine(url)

    with engine.connect() as connection:
        try:
            user_id = user_id_for(connection, args.username)
        except ConfigurationNotFound as exc:
            sys.exit(str(exc))
    found = draft_version(engine, user_id, args.version)
    if found is None:
        sys.exit(f"{args.username} has no draft" +
                 (f" version {args.version}." if args.version else "."))
    version, config = found
    print(f"Draft version {version} for {args.username}:")
    print(json.dumps(config, indent=2) if args.json else summary(config))
    try:
        check_configuration(config)
    except ValueError as exc:
        sys.exit(f"\nIt doesn't pass the configuration check, so it can't be activated:\n{exc}")

    if not args.activate:
        print("\nReviewed only. Add --activate to activate it.")
        return
    _, new_secrets = switch_on(engine, user_id, version)
    print(f"\nActivated version {version}; the previous active version is retired.")
    for new in new_secrets:
        print(f"  {new['destination_id']}: new signing secret (shown once; pass it to the client):")
        print(f"    {new['secret']}")
    if not new_secrets:
        print("  Every webhook kept its existing signing secret.")


if __name__ == "__main__":
    main()
