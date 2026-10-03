import os
import re
from functools import lru_cache
from typing import Any

from dotenv import dotenv_values
from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Keys in Azure App Configuration are namespaced per component, e.g.
# "BACKEND:JWT_SECRET". The prefix is trimmed so they map onto the fields
# below the same way environment variables do.
APP_CONFIG_PREFIX = "BACKEND:"


class AppConfigurationSource(PydanticBaseSettingsSource):
    """Loads settings from Azure App Configuration when
    APP_CONFIG_CONNECTION_STRING is set (as an env var or in .env). Key Vault
    references are resolved with DefaultAzureCredential -- the deployed app's
    managed identity, or your `az login` locally."""

    def get_field_value(self, field, field_name):  # unused: __call__ returns everything at once
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        conn_str = os.environ.get("APP_CONFIG_CONNECTION_STRING") or dotenv_values(".env").get(
            "APP_CONFIG_CONNECTION_STRING"
        )
        if not conn_str:
            return {}

        from azure.appconfiguration.provider import SettingSelector, load
        from azure.identity import DefaultAzureCredential

        config = load(
            connection_string=conn_str,
            selects=[SettingSelector(key_filter=f"{APP_CONFIG_PREFIX}*")],
            trim_prefixes=[APP_CONFIG_PREFIX],
            keyvault_credential=DefaultAzureCredential(),
        )
        return {key.lower(): value for key, value in config.items()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres connection string (Supabase session pooler in dev). Required --
    # there's no local fallback database.
    database_url: str

    jwt_secret: str = "dev-only-change-me"
    jwt_expire_minutes: int = 60 * 24 * 30  # 30 days

    magic_link_expire_minutes: int = 15

    frontend_url: str = "http://localhost:3000"

    resend_api_key: str | None = None
    email_from: str = "3lay <onboarding@resend.dev>"

    session_cookie_name: str = "3lay_session"

    # Domain clients' forwarding addresses live on: a client with username
    # `rolepay-agent` gets `rolepay-agent@<this>`. Must match the domain whose
    # Cloudflare Email Routing catch-all sends to the ingest Worker.
    client_forwarding_domain: str

    # The storage the ingest function writes raw emails to, so deleting an
    # account can also delete that client's stored data. Same values as the
    # function's FUNCTION:INGEST_STORAGE_* / FUNCTION:RAW_CONTAINER_NAME --
    # keep them in step if either changes. Set the connection string, or the
    # account name to connect with DefaultAzureCredential instead.
    ingest_storage_connection_string: str | None = None
    ingest_storage_account_name: str | None = None
    raw_container_name: str = "raw-ingest"

    @field_validator("client_forwarding_domain")
    @classmethod
    def _normalize_domain(cls, value: str) -> str:
        domain = value.strip().lower().lstrip("@")
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", domain):
            raise ValueError(f"CLIENT_FORWARDING_DOMAIN must be a domain like in.3lay.live, got '{value}'")
        return domain

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        """Accept the plain `postgresql://` string Supabase (or any Postgres
        host) hands out, and point SQLAlchemy at the psycopg 3 driver -- by
        default it would look for psycopg2, which isn't installed."""
        value = value.strip()
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        scheme = value.split(":", 1)[0] or "(empty)"
        raise ValueError(f"DATABASE_URL must be a Postgres connection string, got scheme '{scheme}'")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest priority first: a real env var can still override a single
        # value, App Configuration is the normal source of truth, and .env is
        # only a fallback for working offline without App Configuration.
        return (
            init_settings,
            env_settings,
            AppConfigurationSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
