"""The Boardy endpoints: who can reach which conversation, and what's passed to Boardy.

Boardy itself is replaced by a fake (its own tests are in agents/tests/), so
these check the API around it: ownership, the username step, samples,
feedback and how Boardy's errors reach the chat.
"""

import pytest
from fastapi import HTTPException

from app.models import BoardyConversation, ClientConfiguration
from app.services import boardy


class FakeBoardy:
    """Records what the API asked Boardy to do, and returns a fixed view."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.approved_version = None  # set to make the conversation approved, with that version

    def _view(self, *call):
        self.calls.append(call)
        if self.error:
            raise self.error
        return {"status": "ready", "messages": [], "approval": None, "missing": [],
                "approved": self.approved_version is not None,
                "saved_version": self.approved_version, "changes_pending": False}

    def view(self, user_id, conversation_id):
        return self._view("view", user_id, conversation_id)

    def send(self, user_id, username, conversation_id, text, samples):
        return self._view("send", user_id, username, conversation_id, text, samples)

    def answer_approval(self, user_id, conversation_id, approved, comments):
        return self._view("approval", user_id, conversation_id, approved, comments)

    def retry(self, user_id, conversation_id):
        return self._view("retry", user_id, conversation_id)


@pytest.fixture
def fake(monkeypatch):
    fake_boardy = FakeBoardy()
    monkeypatch.setattr(boardy, "conversations", lambda: fake_boardy)
    return fake_boardy


def start(api, user_id="u1"):
    response = api.sign_in(user_id).post("/boardy/conversations")
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_a_conversation_needs_a_session(api, fake):
    assert api.post("/boardy/conversations").status_code == 401
    assert not fake.calls


def test_a_conversation_needs_a_username_first(api, fake):
    response = api.sign_in("u3").post("/boardy/conversations")
    assert response.status_code == 409
    assert not fake.calls


@pytest.mark.parametrize("method, path, body", [
    ("get", "", None),
    ("post", "/messages", {"text": "Hi"}),
    ("post", "/approval", {"approved": True}),
    ("post", "/retry", None),
    ("put", "/feedback", {"text": "Nice"}),
])
def test_nobody_else_can_reach_a_conversation(api, fake, method, path, body):
    conversation_id = start(api, "u1")
    fake.calls.clear()

    other = api.sign_in("u2")
    response = other.request(method, f"/boardy/conversations/{conversation_id}{path}", json=body)

    assert response.status_code == 404
    assert not fake.calls


def test_a_message_goes_to_boardy_as_the_sessions_customer(api, fake, db_session):
    conversation_id = start(api)
    db_session.get(BoardyConversation, conversation_id).samples = [
        {"id": "sample_1", "name": "a.pdf", "content_type": "application/pdf",
         "blob": f"u1/{conversation_id}/sample_1.pdf"}]
    db_session.commit()

    response = api.post(f"/boardy/conversations/{conversation_id}/messages", json={"text": "Hi"})

    assert response.status_code == 200
    assert fake.calls[-1][:5] == ("send", "u1", "rolepay-agent", conversation_id, "Hi")
    assert fake.calls[-1][5][0]["id"] == "sample_1"
    assert response.json()["samples"] == [{"id": "sample_1", "name": "a.pdf"}]


def test_an_upload_is_added_to_the_conversations_samples(api, fake, monkeypatch, db_session):
    conversation_id = start(api)
    stored = []

    def store_sample(samples, user_id, conv_id, name, content_type, data):
        stored.append((len(samples), user_id, conv_id, name, content_type, data))
        return {"id": "sample_1", "name": name, "content_type": content_type, "blob": "b"}
    monkeypatch.setattr(boardy, "store_sample", store_sample)
    monkeypatch.setattr(boardy, "max_sample_bytes", lambda: 1000)

    response = api.post(f"/boardy/conversations/{conversation_id}/samples",
                        files={"file": ("statement.pdf", b"%PDF-1.7", "application/pdf")})

    assert response.status_code == 200, response.text
    assert stored == [(0, "u1", conversation_id, "statement.pdf", "application/pdf", b"%PDF-1.7")]
    assert response.json()["samples"] == [{"id": "sample_1", "name": "statement.pdf"}]
    assert len(db_session.get(BoardyConversation, conversation_id).samples) == 1


def test_feedback_is_saved_and_can_be_cleared(api, fake, db_session):
    conversation_id = start(api)

    saved = api.put(f"/boardy/conversations/{conversation_id}/feedback",
                    json={"text": " Boardy asked the same question twice. "})
    assert saved.json()["feedback"] == "Boardy asked the same question twice."
    assert db_session.get(BoardyConversation, conversation_id).feedback_at is not None

    cleared = api.put(f"/boardy/conversations/{conversation_id}/feedback", json={"text": ""})
    assert cleared.json()["feedback"] is None


def test_boardys_errors_become_messages_for_the_chat(api, fake):
    from boardy.conversations import BoardyUnavailable, WrongState  # pylint: disable=import-outside-toplevel

    conversation_id = start(api)
    url = f"/boardy/conversations/{conversation_id}/messages"

    fake.error = WrongState("Boardy is waiting for your approval.")
    busy = api.post(url, json={"text": "Hi"})
    fake.error = BoardyUnavailable("Boardy couldn't finish that.")
    down = api.post(url, json={"text": "Hi"})

    assert (busy.status_code, busy.json()["detail"]) == (409, "Boardy is waiting for your approval.")
    assert (down.status_code, down.json()["detail"]) == (503, "Boardy couldn't finish that.")


def test_the_list_only_has_the_customers_own_conversations(api, fake):
    mine = start(api, "u1")
    start(api, "u2")

    listed = api.sign_in("u1").get("/boardy/conversations").json()

    assert [c["id"] for c in listed] == [mine]


def test_a_storage_failure_is_a_message_not_a_crash(api, fake, monkeypatch):
    conversation_id = start(api)
    monkeypatch.setattr(boardy, "max_sample_bytes", lambda: 1000)
    monkeypatch.setattr(boardy, "_load_agent_settings", lambda: None)

    def not_allowed(*_args):
        raise PermissionError("AuthorizationPermissionMismatch")
    import boardy.samples as samples  # pylint: disable=import-outside-toplevel
    monkeypatch.setattr(samples, "store_sample", not_allowed)

    response = api.post(f"/boardy/conversations/{conversation_id}/samples",
                        files={"file": ("statement.pdf", b"%PDF-1.7", "application/pdf")})

    assert response.status_code == 503
    assert response.json()["detail"] == "Couldn't store the sample right now. Try again later."


# Approving switches the configuration on

def saved_draft(db_session, user_id="u1", version=1):
    """The draft Boardy saves when the client approves (Boardy is faked here)."""
    db_session.add(ClientConfiguration(user_id=user_id, version=version, status="draft", config={}))
    db_session.commit()


@pytest.fixture
def switching(monkeypatch, db_session):
    """Replaces switching on: marks the version active and returns a secret. Records calls."""
    calls = []

    def switch_on(user_id, version):
        calls.append((user_id, version))
        row = db_session.query(ClientConfiguration).filter_by(user_id=user_id, version=version).one()
        row.status = "active"
        db_session.commit()
        return [{"destination_id": "dest_rolepay_agent_main", "url": "https://hooks.example/3lay",
                 "secret": "whsec_test"}]
    monkeypatch.setattr(boardy, "switch_on", switch_on)
    return calls


def approve(api, conversation_id, approved=True):
    return api.post(f"/boardy/conversations/{conversation_id}/approval",
                    json={"approved": approved, "comments": ""})


def test_approving_switches_the_configuration_on_and_shows_the_secret_once(
        api, fake, db_session, switching):
    conversation_id = start(api)
    saved_draft(db_session)
    fake.approved_version = 1

    approved = approve(api, conversation_id).json()

    assert switching == [("u1", 1)]
    assert approved["active_version"] == 1 and approved["activation_pending"] is False
    assert approved["new_secrets"] == [{"destination_id": "dest_rolepay_agent_main",
                                        "url": "https://hooks.example/3lay", "secret": "whsec_test"}]
    later = api.get(f"/boardy/conversations/{conversation_id}").json()
    assert later["new_secrets"] == []  # never shown again


def test_declining_switches_nothing_on(api, fake, switching):
    conversation_id = start(api)

    declined = approve(api, conversation_id, approved=False).json()

    assert not switching
    assert declined["active_version"] is None and declined["activation_pending"] is False


def test_if_switching_on_fails_the_approval_stands_and_can_be_retried(
        api, fake, db_session, switching, monkeypatch):
    conversation_id = start(api)
    saved_draft(db_session)
    fake.approved_version = 1
    working = boardy.switch_on

    def failing(*_args):
        raise HTTPException(status_code=503, detail=boardy.SWITCH_ON_FAILED)
    monkeypatch.setattr(boardy, "switch_on", failing)
    approved = approve(api, conversation_id)

    assert approved.status_code == 200
    assert approved.json()["activation_pending"] is True
    assert approved.json()["activation_error"] == boardy.SWITCH_ON_FAILED

    monkeypatch.setattr(boardy, "switch_on", working)
    retried = api.post(f"/boardy/conversations/{conversation_id}/activate").json()
    assert retried["active_version"] == 1 and retried["new_secrets"][0]["secret"] == "whsec_test"


def test_nothing_to_switch_on_is_refused(api, fake, switching):
    conversation_id = start(api)

    response = api.post(f"/boardy/conversations/{conversation_id}/activate")

    assert response.status_code == 409
    assert not switching



def test_if_boardys_settings_cant_load_the_chat_gets_a_503_not_a_hang(api, monkeypatch):
    import settings as agent_settings  # pylint: disable=import-outside-toplevel

    def unreachable():
        raise RuntimeError("Failed to load configurations from endpoint")
    monkeypatch.setattr(boardy, "_settings_loaded", False)
    monkeypatch.setattr(agent_settings, "load_settings", unreachable)

    response = api.sign_in("u1").post("/boardy/conversations")

    assert response.status_code == 503
    assert response.json()["detail"] == boardy.UNAVAILABLE
