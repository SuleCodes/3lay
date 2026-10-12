"""Client configurations: checking them, saving new versions, and loading the active one.

A configuration (see tools/Planner.md, "Client configuration"):

    {
      "processing": {"max_attempts": 3},
      "document_types": [
        {"name": "income_statement",
         "instructions": "...",
         "schema": {... JSON Schema with "$id": "<name>/<version>" ...},
         "rules": [... rules as data ...]}
      ],
      "analyses": [],
      "destinations": [{"id": "dest_...", "type": "webhook", "url": "...", "events": [...]}]
    }

For now exactly one document type (classification between several comes
later) and no analyses. Versions are never edited: saving makes a new version,
and activating it retires the previous one.
"""

import uuid
from datetime import datetime, timezone

import jsonschema
from sqlalchemy import func, select, update

from noti.destinations import check_destinations
from rules import check_rule_definitions
from store.db import client_configurations, users

DEFAULT_MAX_ATTEMPTS = 3

CONFIGURATION_SCHEMA = {
    "type": "object",
    "required": ["document_types", "destinations"],
    "additionalProperties": False,
    "properties": {
        "processing": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"max_attempts": {"type": "integer", "minimum": 1, "maximum": 5}},
        },
        "document_types": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,  # until classification can choose between several
            "items": {
                "type": "object",
                "required": ["name", "instructions", "schema"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "description": {"type": "string"},
                    "instructions": {"type": "string", "minLength": 1},
                    "schema": {"type": "object", "required": ["$id"]},
                    "rules": {"type": "array"},
                },
            },
        },
        "analyses": {"type": "array", "maxItems": 0},  # none supported yet
        "destinations": {"type": "array"},
    },
}


class ConfigurationNotFound(LookupError):
    """No such client, or the client has no active configuration."""


def check_configuration(config):
    """Raises ValueError listing every problem; returns the configuration if fine.

    The same check runs whoever saves a configuration: this script now, the
    dashboard and Boardy later.
    """
    problems = [
        f"{'.'.join(str(p) for p in error.absolute_path) or 'configuration'}: {error.message}"
        for error in jsonschema.Draft202012Validator(CONFIGURATION_SCHEMA).iter_errors(config)
    ]
    if problems:
        raise ValueError("Invalid configuration:\n  " + "\n  ".join(problems))

    for document_type in config["document_types"]:
        try:
            jsonschema.Draft202012Validator.check_schema(document_type["schema"])
        except jsonschema.SchemaError as exc:
            problems.append(f"document type {document_type['name']}: schema: {exc.message}")
        try:
            check_rule_definitions(document_type.get("rules", []))
        except ValueError as exc:
            problems.append(f"document type {document_type['name']}: {exc}")
    try:
        check_destinations(config["destinations"])
    except ValueError as exc:
        problems.append(str(exc))
    if problems:
        raise ValueError("Invalid configuration:\n  " + "\n  ".join(problems))
    return config


def run_settings(config, version):
    """The parts of Orchy's starting state that come from the configuration."""
    document_type = config["document_types"][0]
    return {
        "config_version": version,
        "document_type": document_type["name"],
        "instructions": document_type["instructions"],
        "output_schema": document_type["schema"],
        "schema_name": document_type["name"],
        "rules": document_type.get("rules", []),
        "destinations": config["destinations"],
        "max_attempts": config.get("processing", {}).get("max_attempts", DEFAULT_MAX_ATTEMPTS),
    }


def user_id_for(connection, username):
    user_id = connection.execute(
        select(users.c.id).where(users.c.username == username.lower())).scalar()
    if user_id is None:
        raise ConfigurationNotFound(f"No client with the username {username!r}")
    return user_id


def load_active_configuration(engine, username):
    """(configuration, version, user_id) of the client's active configuration."""
    with engine.connect() as connection:
        user_id = user_id_for(connection, username)
        row = connection.execute(
            select(client_configurations.c.config, client_configurations.c.version)
            .where(client_configurations.c.user_id == user_id,
                   client_configurations.c.status == "active")).first()
    if row is None:
        raise ConfigurationNotFound(f"{username} has no active configuration")
    return row.config, row.version, user_id


def save_configuration(engine, username, config, activate=True):
    """Checks the configuration and saves it as the client's next version. Returns the version.

    With activate (the default, until approval exists), the new version becomes
    active and the previously active one is retired, in one transaction.
    """
    check_configuration(config)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:  # one transaction: all or nothing
        user_id = user_id_for(connection, username)
        latest = connection.execute(
            select(func.max(client_configurations.c.version))
            .where(client_configurations.c.user_id == user_id)).scalar()
        version = (latest or 0) + 1
        if activate:
            connection.execute(
                update(client_configurations)
                .where(client_configurations.c.user_id == user_id,
                       client_configurations.c.status == "active")
                .values(status="retired"))
        connection.execute(client_configurations.insert().values(
            id=str(uuid.uuid4()), user_id=user_id, version=version,
            status="active" if activate else "draft", config=config,
            created_at=now, activated_at=now if activate else None))
    return version
