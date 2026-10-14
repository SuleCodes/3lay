"""The webhooks section: listing the client's live webhooks and rotating a signing secret.

Rotating itself is the agents' secret store (tested in agents/tests/); here it's
faked, to check the API around it: whose webhooks, and what's shown.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import ClientConfiguration, DestinationSecret
from app.services import boardy

HOOK = {"id": "dest_rolepay_agent_main", "type": "webhook", "url": "https://hooks.rolepay.example/3lay",
        "events": ["event.completed", "event.failed"]}


def configure(db_session, user_id="u1", status="active", destinations=(HOOK,), version=1):
    db_session.add(ClientConfiguration(user_id=user_id, version=version, status=status,
                                       config={"destinations": list(destinations)}))
    db_session.commit()


def give_secret(db_session, destination_id=HOOK["id"], user_id="u1", **fields):
    db_session.add(DestinationSecret(destination_id=destination_id, user_id=user_id,
                                     ciphertext="encrypted", prefix="whsec_AbCd", **fields))
    db_session.commit()


def test_no_live_configuration_means_no_webhooks(api, db_session):
    configure(db_session, status="draft")  # drafts don't count

    listed = api.sign_in("u1").get("/webhooks").json()

    assert listed == {"active_version": None, "webhooks": []}


def test_the_live_webhooks_are_listed_with_only_their_secret_prefix(api, db_session):
    configure(db_session)
    give_secret(db_session)

    listed = api.sign_in("u1").get("/webhooks").json()

    assert listed["active_version"] == 1
    [hook] = listed["webhooks"]
    assert (hook["id"], hook["url"], hook["events"]) == (HOOK["id"], HOOK["url"], HOOK["events"])
    assert hook["secret_prefix"] == "whsec_AbCd"
    assert "encrypted" not in str(listed)


def test_an_old_secrets_grace_period_is_shown_only_while_it_lasts(api, db_session):
    configure(db_session)
    give_secret(db_session, previous_ciphertext="old",
                previous_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    [hook] = api.sign_in("u1").get("/webhooks").json()["webhooks"]

    assert hook["previous_secret_expires_at"] is None


def test_each_client_sees_only_their_own_webhooks(api, db_session):
    configure(db_session, user_id="u2",
              destinations=[{**HOOK, "id": "dest_other_client_main", "url": "https://other.example"}])

    assert api.sign_in("u1").get("/webhooks").json()["webhooks"] == []


@pytest.fixture
def rotating(monkeypatch, db_session):
    """Replaces the secret store's rotation: updates the row like it would, records calls."""
    calls = []

    def rotate_secret(user_id, destination_id):
        calls.append((user_id, destination_id))
        row = db_session.get(DestinationSecret, destination_id)
        row.previous_ciphertext, row.prefix = row.ciphertext, "whsec_NeW1"
        row.rotated_at = datetime.now(timezone.utc)
        row.previous_expires_at = row.rotated_at + timedelta(hours=24)
        db_session.commit()
        return "whsec_NeW1-the-whole-secret"
    monkeypatch.setattr(boardy, "rotate_secret", rotate_secret)
    return calls


def test_rotating_shows_the_new_secret_once(api, db_session, rotating):
    configure(db_session)
    give_secret(db_session)

    rotated = api.sign_in("u1").post(f"/webhooks/{HOOK['id']}/rotate-secret").json()

    assert rotating == [("u1", HOOK["id"])]
    assert rotated["secret"] == "whsec_NeW1-the-whole-secret"
    assert rotated["webhook"]["secret_prefix"] == "whsec_NeW1"
    assert rotated["webhook"]["previous_secret_expires_at"] is not None
    listed = api.get("/webhooks").json()
    assert "the-whole-secret" not in str(listed)


def test_only_a_live_webhook_of_your_own_can_be_rotated(api, db_session, rotating):
    configure(db_session, user_id="u2",
              destinations=[{**HOOK, "id": "dest_other_client_main"}])

    mine = api.sign_in("u1")
    assert mine.post("/webhooks/dest_other_client_main/rotate-secret").status_code == 404
    assert mine.post("/webhooks/dest_unknown/rotate-secret").status_code == 404
    assert not rotating
