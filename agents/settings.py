"""Loads the agents' settings into the environment: local first, then Azure App Configuration.

The same convention as the backend and the ingest function: App Configuration
is the shared source of truth, with keys prefixed by component ("AGENTS:"
here, trimmed, so "AGENTS:OBED_MODEL" becomes OBED_MODEL). Anything set
locally wins, for this machine only:

    1. real environment variables
    2. agents/.env
    3. App Configuration "AGENTS:*" keys (Key Vault references resolved with
       DefaultAzureCredential: the deployed job's managed identity, or your
       `az login` locally)

The only setting needed to reach App Configuration is
APP_CONFIG_CONNECTION_STRING (in the environment or agents/.env). Without it,
only local settings are used.

Settings end up as environment variables, which is what the rest of the code
reads (os.environ). Call load_settings() once, at the start of a script or job.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

AGENTS_DIR = Path(__file__).resolve().parent
APP_CONFIG_PREFIX = "AGENTS:"

_loaded = set()


def app_configuration(connection_string, key_filter, trim_prefix):
    """{name: value} from App Configuration for keys matching key_filter."""
    # Imported here so tests and offline runs don't need the Azure packages loaded.
    from azure.appconfiguration.provider import (  # pylint: disable=import-outside-toplevel
        SettingSelector, load)
    from azure.identity import DefaultAzureCredential  # pylint: disable=import-outside-toplevel

    config = load(
        connection_string=connection_string,
        selects=[SettingSelector(key_filter=key_filter)],
        trim_prefixes=[trim_prefix] if trim_prefix else [],
        keyvault_credential=DefaultAzureCredential(),
    )
    return dict(config.items())


def load_settings(*borrowed):
    """Fills in the environment from agents/.env, then App Configuration, without overriding.

    borrowed are (App Configuration key, environment variable) pairs to read
    from other components' keys, e.g. ("BACKEND:DATABASE_URL",
    "ADMIN_DATABASE_URL") for the configuration script, which writes with the
    backend's database connection. Each set of keys is only fetched once.
    """
    load_dotenv(AGENTS_DIR / ".env")  # never overrides a real environment variable
    connection_string = os.environ.get("APP_CONFIG_CONNECTION_STRING")
    if not connection_string:
        return

    if APP_CONFIG_PREFIX not in _loaded:
        for name, value in app_configuration(connection_string, f"{APP_CONFIG_PREFIX}*",
                                             APP_CONFIG_PREFIX).items():
            os.environ.setdefault(name, value)  # local settings win
        _loaded.add(APP_CONFIG_PREFIX)

    for key, variable in borrowed:
        if key in _loaded:
            continue
        value = app_configuration(connection_string, key, None).get(key)
        if value is not None:
            os.environ.setdefault(variable, value)
        _loaded.add(key)
