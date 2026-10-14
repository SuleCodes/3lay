"""Switching a client's approved draft on: activate it, and give new webhooks their signing secret.

Used when a client approves their configuration in Boardy's chat (the
backend, with its own connection: Boardy's role can only save drafts) and by
scripts/activate_configuration.py. New secrets are returned to be shown once,
to the client, and are never stored anywhere but encrypted in the database.
"""

from sqlalchemy import select

from store.configurations import ConfigurationNotFound, activate_configuration
from store.db import client_configurations
from store.secrets import create_or_rotate_secret, has_secret


def configuration_version(engine, user_id, version):
    """(configuration, status) of one of the client's versions."""
    with engine.connect() as connection:
        row = connection.execute(
            select(client_configurations.c.config, client_configurations.c.status)
            .where(client_configurations.c.user_id == user_id,
                   client_configurations.c.version == version)).first()
    if row is None:
        raise ConfigurationNotFound(f"There's no version {version}")
    return row.config, row.status


def switch_on(engine, user_id, version):
    """Activates the version and creates secrets for its webhooks that have none.

    Returns (activated, new_secrets): activated is False if it was already
    active; new_secrets is [{"destination_id", "url", "secret"}], to show once.
    Existing secrets are kept, so a new version never breaks the client's
    verification. Safe to call again: nothing is activated or created twice.
    """
    config, _ = configuration_version(engine, user_id, version)
    activated = activate_configuration(engine, user_id, version)
    new_secrets = []
    for destination in config["destinations"]:
        if has_secret(engine, destination["id"]):
            continue
        new_secrets.append({"destination_id": destination["id"], "url": destination["url"],
                            "secret": create_or_rotate_secret(engine, user_id, destination["id"])})
    return activated, new_secrets
