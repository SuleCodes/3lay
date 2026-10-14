"""The database layer: encryption, configurations and destination secrets.

Runs against an in-memory SQLite database with an attached "app" schema, so
nothing touches Supabase. The real tables are made by the backend's migration;
store/db.py describes the same tables.
"""

import base64
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from conftest import ENCRYPTION_KEY
from noti import destinations as noti_destinations
from store import db
from store.configurations import (ConfigurationNotFound, activate_configuration,
                                  check_configuration, latest_draft, load_active_configuration,
                                  run_settings, save_configuration, save_draft)
from store.db import client_configurations, destination_secrets
from store.secret_box import SecretBoxError, decrypt, encrypt, generate_key
from store.secrets import create_or_rotate_secret, has_secret, signing_secrets

KEY = ENCRYPTION_KEY

CONFIG = {
    "processing": {"max_attempts": 2},
    "document_types": [{
        "name": "income_statement",
        "instructions": "Extract it.",
        "schema": {"$id": "test-statement/1", "type": "object"},
        "rules": [{"id": "dates_plausible", "type": "date_within", "fields": ["issued_on"],
                   "days_before": 1095, "days_after": 31, "on_fail": "needs_review"}],
    }],
    "analyses": [],
    "destinations": [{"id": "dest_test_main", "type": "webhook",
                      "url": "https://hooks.example.com/3lay", "events": ["event.completed"]}],
}


@pytest.fixture
def engine(database):
    """The shared in-memory database (tests/conftest.py)."""
    return database


# Encryption

def test_a_secret_encrypts_and_decrypts():
    stored = encrypt("whsec_abc", KEY, 1, "dest_a")

    assert stored.startswith("v1:1:")
    assert "whsec_abc" not in stored
    assert decrypt(stored, {1: KEY}, "dest_a") == "whsec_abc"


def test_the_same_secret_never_encrypts_the_same_way_twice():
    assert encrypt("whsec_abc", KEY, 1, "dest_a") != encrypt("whsec_abc", KEY, 1, "dest_a")


def test_a_ciphertext_only_decrypts_for_its_own_destination():
    """Moving one client's encrypted secret into another client's row doesn't work."""
    stored = encrypt("whsec_abc", KEY, 1, "dest_a")

    with pytest.raises(SecretBoxError, match="wrong row"):
        decrypt(stored, {1: KEY}, "dest_b")


def test_the_wrong_key_or_tampering_is_detected():
    stored = encrypt("whsec_abc", KEY, 1, "dest_a")
    tampered = stored[:-4] + ("AAAA" if not stored.endswith("AAAA") else "BBBB")

    with pytest.raises(SecretBoxError):
        decrypt(stored, {1: generate_key()}, "dest_a")
    with pytest.raises(SecretBoxError):
        decrypt(tampered, {1: KEY}, "dest_a")


def test_the_key_version_picks_the_key_so_keys_can_be_rotated():
    old, new = generate_key(), generate_key()
    stored_with_old = encrypt("whsec_abc", old, 1, "dest_a")

    assert decrypt(stored_with_old, {1: old, 2: new}, "dest_a") == "whsec_abc"
    with pytest.raises(SecretBoxError, match="No key for version 1"):
        decrypt(stored_with_old, {2: new}, "dest_a")


def test_keys_must_be_32_bytes():
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt("x", base64.b64encode(b"short").decode(), 1, "dest_a")


# Checking configurations

def test_a_valid_configuration_passes():
    assert check_configuration(CONFIG) is CONFIG


@pytest.mark.parametrize("change, problem", [
    ({"document_types": []}, "document_types"),
    ({"analyses": [{"id": "match"}]}, "analyses"),
    ({"extra": True}, "extra"),
    ({"processing": {"max_attempts": 10}}, "max_attempts"),
])
def test_a_bad_configuration_shape_is_rejected(change, problem):
    with pytest.raises(ValueError, match="Invalid configuration") as error:
        check_configuration({**CONFIG, **change})
    assert problem in str(error.value)


def test_the_rules_and_destinations_inside_are_checked_too():
    document_type = {**CONFIG["document_types"][0], "rules": [{"id": "x", "type": "average"}]}
    destination = {**CONFIG["destinations"][0], "secret": "whsec_x"}

    with pytest.raises(ValueError) as error:
        check_configuration({**CONFIG, "document_types": [document_type],
                             "destinations": [destination]})
    assert "unknown type 'average'" in str(error.value)
    assert "secret" in str(error.value)  # secrets never go in a configuration


def test_an_invalid_json_schema_is_rejected():
    document_type = {**CONFIG["document_types"][0],
                     "schema": {"$id": "x/1", "type": "not-a-type"}}

    with pytest.raises(ValueError, match="schema"):
        check_configuration({**CONFIG, "document_types": [document_type]})


def test_run_settings_fill_orchys_starting_state():
    settings = run_settings(CONFIG, 4)

    assert settings == {
        "config_version": 4,
        "document_type": "income_statement",
        "instructions": "Extract it.",
        "output_schema": CONFIG["document_types"][0]["schema"],
        "schema_name": "income_statement",
        "rules": CONFIG["document_types"][0]["rules"],
        "destinations": CONFIG["destinations"],
        "max_attempts": 2,
    }


# Saving and loading versions

def test_saving_makes_the_next_version_active_and_retires_the_previous(engine):
    assert save_configuration(engine, "rolepay-agent", CONFIG) == 1
    changed = {**CONFIG, "processing": {"max_attempts": 3}}
    assert save_configuration(engine, "rolepay-agent", changed) == 2

    config, version, user_id = load_active_configuration(engine, "rolepay-agent")
    assert (version, user_id, config["processing"]["max_attempts"]) == (2, "u1", 3)
    with engine.connect() as connection:
        rows = connection.execute(select(client_configurations.c.version,
                                         client_configurations.c.status)
                                  .order_by(client_configurations.c.version)).all()
    assert [tuple(r) for r in rows] == [(1, "retired"), (2, "active")]


def test_each_client_has_its_own_versions(engine):
    save_configuration(engine, "rolepay-agent", CONFIG)

    assert save_configuration(engine, "other-client", CONFIG) == 1


def test_an_invalid_configuration_is_never_saved(engine):
    with pytest.raises(ValueError):
        save_configuration(engine, "rolepay-agent", {**CONFIG, "document_types": []})
    with pytest.raises(ConfigurationNotFound):
        load_active_configuration(engine, "rolepay-agent")


def test_the_database_allows_only_one_active_version_per_client(engine):
    save_configuration(engine, "rolepay-agent", CONFIG)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(client_configurations.insert().values(
            id="sneaky", user_id="u1", version=99, status="active", config=CONFIG,
            created_at=datetime.now(timezone.utc)))


def test_unknown_clients_and_clients_without_a_configuration(engine):
    with pytest.raises(ConfigurationNotFound, match="No client"):
        load_active_configuration(engine, "nobody")
    with pytest.raises(ConfigurationNotFound, match="no active configuration"):
        load_active_configuration(engine, "other-client")


def test_usernames_are_matched_case_insensitively(engine):
    save_configuration(engine, "rolepay-agent", CONFIG)

    assert load_active_configuration(engine, "RolePay-Agent")[1] == 1


# Destination secrets

def test_a_new_secret_is_stored_encrypted_and_read_back(engine):
    secret = create_or_rotate_secret(engine, "u1", "dest_test_main")

    assert secret.startswith("whsec_")
    assert signing_secrets(engine, "dest_test_main") == [secret]
    assert has_secret(engine, "dest_test_main")
    with engine.connect() as connection:
        row = connection.execute(select(destination_secrets)).one()
    assert secret not in row.ciphertext  # never stored in the clear
    assert row.prefix == secret[:10]


def test_a_rotation_signs_with_both_secrets_until_the_grace_period_ends(engine):
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    old = create_or_rotate_secret(engine, "u1", "dest_test_main", now=now)
    new = create_or_rotate_secret(engine, "u1", "dest_test_main", now=now)

    assert signing_secrets(engine, "dest_test_main", now=now + timedelta(hours=1)) == [new, old]
    assert signing_secrets(engine, "dest_test_main", now=now + timedelta(hours=25)) == [new]


def test_a_destination_id_cant_be_taken_over_by_another_client(engine):
    create_or_rotate_secret(engine, "u1", "dest_test_main")

    with pytest.raises(ValueError, match="another client"):
        create_or_rotate_secret(engine, "u2", "dest_test_main")


def test_no_secret_means_nothing_to_sign_with(engine):
    assert not signing_secrets(engine, "dest_unknown")
    assert not has_secret(engine, "dest_unknown")


def test_delivery_uses_a_local_secret_first_then_the_database(engine, monkeypatch):
    stored = create_or_rotate_secret(engine, "u1", "dest_test_main")
    monkeypatch.setattr(db, "database_configured", lambda: True)
    monkeypatch.setattr(db, "engine", lambda: engine)

    assert noti_destinations.destination_secret("dest_test_main") == [stored]
    monkeypatch.setenv("NOTI_SECRET_DEST_TEST_MAIN", "whsec_local")
    assert noti_destinations.destination_secret("dest_test_main") == "whsec_local"


# Drafts (saved by Boardy when the client approves) and activating them (3lay)

def changed(config, max_attempts):
    return {**config, "processing": {"max_attempts": max_attempts}}


def statuses(engine, user_id):
    with engine.connect() as connection:
        return dict(connection.execute(
            select(client_configurations.c.version, client_configurations.c.status)
            .where(client_configurations.c.user_id == user_id)).all())


def test_a_draft_is_saved_but_not_used(engine):
    assert save_draft(engine, "u1", CONFIG) == (1, True)

    assert latest_draft(engine, "u1") == (1, CONFIG)
    with pytest.raises(ConfigurationNotFound):
        load_active_configuration(engine, "rolepay-agent")


def test_saving_the_same_draft_again_saves_nothing_new(engine):
    save_draft(engine, "u1", CONFIG)

    assert save_draft(engine, "u1", CONFIG) == (1, False)
    assert save_draft(engine, "u1", changed(CONFIG, 4)) == (2, True)
    assert statuses(engine, "u1") == {1: "draft", 2: "draft"}


def test_a_draft_is_checked_before_saving(engine):
    with pytest.raises(ValueError):
        save_draft(engine, "u1", {**CONFIG, "destinations": "nowhere"})
    assert latest_draft(engine, "u1") is None


def test_activating_a_draft_retires_the_active_version(engine):
    save_configuration(engine, "rolepay-agent", CONFIG)  # version 1, active
    save_draft(engine, "u1", changed(CONFIG, 4))         # version 2, draft

    assert activate_configuration(engine, "u1", 2) is True

    assert statuses(engine, "u1") == {1: "retired", 2: "active"}
    config, version, _ = load_active_configuration(engine, "rolepay-agent")
    assert (version, config["processing"]) == (2, {"max_attempts": 4})
    assert activate_configuration(engine, "u1", 2) is False  # already active


def test_only_a_clients_own_draft_can_be_activated(engine):
    save_configuration(engine, "rolepay-agent", CONFIG)
    save_draft(engine, "u1", changed(CONFIG, 4))
    activate_configuration(engine, "u1", 2)

    with pytest.raises(ValueError, match="Version 1 is retired"):
        activate_configuration(engine, "u1", 1)
    with pytest.raises(ConfigurationNotFound):
        activate_configuration(engine, "u2", 2)  # another client's version



# Switching an approved draft on

def test_switching_on_activates_and_creates_a_secret_once(engine):
    from store.activation import switch_on  # pylint: disable=import-outside-toplevel

    save_draft(engine, "u1", CONFIG)

    activated, secrets = switch_on(engine, "u1", 1)
    assert activated is True
    assert [(s["destination_id"], s["url"]) for s in secrets] == [
        ("dest_test_main", "https://hooks.example.com/3lay")]
    assert secrets[0]["secret"].startswith("whsec_")
    assert statuses(engine, "u1") == {1: "active"}

    assert switch_on(engine, "u1", 1) == (False, [])  # again: nothing new


def test_switching_on_keeps_existing_secrets(engine):
    from store.activation import switch_on  # pylint: disable=import-outside-toplevel

    save_configuration(engine, "rolepay-agent", CONFIG)
    create_or_rotate_secret(engine, "u1", "dest_test_main")
    save_draft(engine, "u1", changed(CONFIG, 4))

    assert switch_on(engine, "u1", 2) == (True, [])
    assert statuses(engine, "u1") == {1: "retired", 2: "active"}
