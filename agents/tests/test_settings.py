"""Settings: real environment first, then agents/.env, then App Configuration.

App Configuration is faked, so nothing reaches Azure.
"""

import os

import pytest

import settings

APP_CONFIG = {
    "AGENTS:OBED_MODEL": "app-config/model",
    "AGENTS:DATABASE_URL": "postgresql://agents_pipeline@db/app",
    "BACKEND:DATABASE_URL": "postgresql://owner@db/app",
}


@pytest.fixture
def app_config(tmp_path, monkeypatch):
    """A fake App Configuration, an empty agents/.env, and a clean slate."""
    calls = []

    def fake(connection_string, key_filter, trim_prefix):
        calls.append(key_filter)
        prefix = key_filter.rstrip("*")
        return {(k[len(trim_prefix):] if trim_prefix else k): v
                for k, v in APP_CONFIG.items()
                if (k.startswith(prefix) if key_filter.endswith("*") else k == key_filter)}

    monkeypatch.setattr(settings, "app_configuration", fake)
    monkeypatch.setattr(settings, "AGENTS_DIR", tmp_path)
    monkeypatch.setattr(settings, "_loaded", set())
    for name in ("OBED_MODEL", "DATABASE_URL", "ADMIN_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_CONFIG_CONNECTION_STRING", "Endpoint=https://fake;Id=x;Secret=y")
    return calls


def test_app_configuration_fills_in_agents_keys_with_the_prefix_trimmed(app_config):
    settings.load_settings()

    assert os.environ["OBED_MODEL"] == "app-config/model"
    assert os.environ["DATABASE_URL"] == "postgresql://agents_pipeline@db/app"
    assert "ADMIN_DATABASE_URL" not in os.environ  # other components' keys aren't loaded


def test_a_real_environment_variable_wins(app_config, monkeypatch):
    monkeypatch.setenv("OBED_MODEL", "my/local-model")

    settings.load_settings()

    assert os.environ["OBED_MODEL"] == "my/local-model"


def test_agents_env_file_wins_over_app_configuration(app_config, tmp_path):
    (tmp_path / ".env").write_text("OBED_MODEL=from-dotenv/model\n", encoding="utf-8")

    settings.load_settings()

    assert os.environ["OBED_MODEL"] == "from-dotenv/model"


def test_a_borrowed_key_reads_another_components_setting(app_config):
    settings.load_settings(("BACKEND:DATABASE_URL", "ADMIN_DATABASE_URL"))

    assert os.environ["ADMIN_DATABASE_URL"] == "postgresql://owner@db/app"
    # The pipeline's own connection is still its limited role, not the backend's.
    assert os.environ["DATABASE_URL"] == "postgresql://agents_pipeline@db/app"


def test_app_configuration_is_only_asked_once(app_config):
    settings.load_settings()
    settings.load_settings()

    assert app_config == ["AGENTS:*"]


def test_without_a_connection_string_only_local_settings_are_used(app_config, monkeypatch):
    monkeypatch.delenv("APP_CONFIG_CONNECTION_STRING")

    settings.load_settings()

    assert app_config == []
    assert "OBED_MODEL" not in os.environ
