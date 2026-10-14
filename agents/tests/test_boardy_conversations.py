"""Boardy behind the API: the conversation service the backend calls, and sample uploads.

The same scripted model as test_boardy.py, an in-memory checkpointer in place
of Postgres, and the test database for approved drafts.
"""

import base64

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

from boardy import graph as boardy_graph
from boardy import tools as boardy_tools
from boardy.conversations import (BoardyUnavailable, ConversationBusy, Conversations, WrongState,
                                  chat_messages, conversation_key, plain_postgres_url)
from boardy.samples import MAX_SAMPLES, sample_bytes, store_sample
from conftest import pdf_bytes
from test_boardy import COMPLETE_DRAFT, ScriptedModel, calls, say, submit

USER = "u1"  # rolepay-agent in the test database


class FailingModel:
    def invoke(self, _messages):
        raise ConnectionError("provider down")


@pytest.fixture
def boardy(monkeypatch, database):
    """(conversations, model): the service with a scripted model; set model.replies per test."""
    model = ScriptedModel()
    monkeypatch.setattr(boardy_graph, "boardy_model", lambda: model)
    monkeypatch.setattr(boardy_tools, "onboarding_db", lambda: database)
    return Conversations(InMemorySaver()), model


def send(conversations, text, conversation="c1", samples=None):
    return conversations.send(USER, "rolepay-agent", conversation, text, samples)


def test_a_turn_returns_what_the_client_sees(boardy):
    conversations, model = boardy
    model.replies += [calls(("show_draft", {})), say("What do you send us?")]

    view = send(conversations, "Hi")

    assert view["status"] == "ready"
    assert view["messages"] == [
        {"role": "client", "text": "Hi", "steps": []},
        {"role": "boardy", "text": "What do you send us?",
         "steps": ["Checked the setup so far"]}]
    assert "the document type" in view["missing"][0]


def test_the_chat_hides_tool_calls_and_results():
    shown = chat_messages([calls(("set_schema", {"json_schema": {}})), say("Done.")])
    assert shown == [{"role": "boardy", "text": "Done.", "steps": ["Recorded the fields to extract"]}]


def test_each_customer_only_reaches_their_own_conversations(boardy):
    conversations, model = boardy
    model.replies += [say("Hello Rolepay.")]
    send(conversations, "Hi")

    assert conversations.view("u2", "c1")["messages"] == []


def test_the_samples_sent_with_a_message_replace_the_list(boardy):
    conversations, model = boardy
    model.replies += [say("One sample."), say("Two samples.")]
    first = [{"id": "sample_1", "name": "a.pdf", "content_type": "application/pdf",
              "blob": "u1/c1/sample_1.pdf"}]
    second = first + [{**first[0], "id": "sample_2", "blob": "u1/c1/sample_2.pdf"}]

    send(conversations, "Hi", samples=first)
    send(conversations, "Added another", samples=second)

    config = boardy_graph.thread_config(USER, "c1")
    assert conversations.graph.get_state(config).values["samples"] == second
    assert "sample_2 (a.pdf)" in model.seen[1][0].content


def test_approval_pauses_the_chat_until_it_is_answered(boardy):
    conversations, model = boardy
    model.replies += [say("Hello."), submit(), say("Thanks! 3lay will switch it on.")]
    send(conversations, "Hi")
    conversations.graph.update_state(boardy_graph.thread_config(USER, "c1"),
                                     {"draft": COMPLETE_DRAFT})

    paused = send(conversations, "Please submit it")
    assert paused["status"] == "awaiting_approval"
    assert paused["approval"] == {"summary": "Income statements to your webhook."}
    with pytest.raises(WrongState, match="waiting for your approval"):
        send(conversations, "Hello?")

    done = conversations.answer_approval(USER, "c1", True, "")
    assert done["status"] == "ready"
    assert done["approved"] is True and done["saved_version"] == 1
    assert done["messages"][-1]["text"] == "Thanks! 3lay will switch it on."


def test_answering_with_no_question_is_refused(boardy):
    conversations, model = boardy
    model.replies += [say("Hello.")]
    send(conversations, "Hi")

    with pytest.raises(WrongState):
        conversations.answer_approval(USER, "c1", True)
    with pytest.raises(WrongState):
        conversations.retry(USER, "c1")
    with pytest.raises(WrongState):
        send(conversations, "   ")


def test_a_failed_turn_keeps_the_message_and_can_be_retried(boardy, monkeypatch):
    conversations, model = boardy
    monkeypatch.setattr(boardy_graph, "boardy_model", FailingModel)

    with pytest.raises(BoardyUnavailable):
        send(conversations, "Hi")
    failed = conversations.view(USER, "c1")
    assert failed["status"] == "failed"
    assert failed["messages"] == [{"role": "client", "text": "Hi", "steps": []}]
    with pytest.raises(WrongState, match="retry"):
        send(conversations, "Hi again")

    monkeypatch.setattr(boardy_graph, "boardy_model", lambda: model)
    model.replies += [say("Sorry about that. Hello!")]
    view = conversations.retry(USER, "c1")

    assert view["status"] == "ready"
    assert [m["text"] for m in view["messages"]] == ["Hi", "Sorry about that. Hello!"]


def test_one_turn_at_a_time_per_conversation(boardy):
    conversations, _ = boardy
    lock = conversations._lock(f"{USER}:c1")  # pylint: disable=protected-access
    lock.acquire()
    try:
        with pytest.raises(ConversationBusy):
            send(conversations, "Hi")
    finally:
        lock.release()


# Uploaded samples

class Container:
    """Stands in for the Blob Storage container."""

    def __init__(self):
        self.blobs = {}

    def upload_blob(self, name, data, overwrite=False):
        assert overwrite
        self.blobs[name] = data

    def download_blob(self, name):
        data = self.blobs[name]
        return type("Download", (), {"readall": lambda self: data})()


def test_an_upload_is_stored_under_the_customer_and_conversation():
    container, data = Container(), pdf_bytes()

    sample = store_sample([], USER, "c1", "../../My statement.pdf", "application/pdf", data,
                          container=container)

    assert sample == {"id": "sample_1", "name": "My statement.pdf",
                      "content_type": "application/pdf", "blob": "u1/c1/sample_1.pdf"}
    assert sample_bytes(sample, container=container) == data


@pytest.mark.parametrize("content_type, data, problem", [
    ("text/plain", b"hello", "only PDF, JPEG and PNG"),
    ("application/pdf", b"not a pdf", "couldn't be opened"),
])
def test_an_unusable_upload_is_refused(content_type, data, problem):
    container = Container()
    with pytest.raises(ValueError, match=problem):
        store_sample([], USER, "c1", "x", content_type, data, container=container)
    assert not container.blobs


def test_uploads_are_limited():
    full = [{"id": f"sample_{n}"} for n in range(1, MAX_SAMPLES + 1)]
    with pytest.raises(ValueError, match=f"up to {MAX_SAMPLES} samples"):
        store_sample(full, USER, "c1", "x.pdf", "application/pdf", pdf_bytes(),
                     container=Container())


# Saving conversations

def test_the_conversation_key_must_be_32_bytes(monkeypatch):
    monkeypatch.setenv("CONVERSATION_ENCRYPTION_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ValueError):
        conversation_key()


def test_saved_conversations_are_encrypted(monkeypatch):
    key = bytes(range(32))
    monkeypatch.setenv("CONVERSATION_ENCRYPTION_KEY", base64.b64encode(key).decode())
    serde = EncryptedSerializer.from_pycryptodome_aes(key=conversation_key())

    kind, data = serde.dumps_typed({"text": "My NI number is on the payslip"})

    assert b"payslip" not in data
    assert serde.loads_typed((kind, data)) == {"text": "My NI number is on the payslip"}


def test_postgres_urls_lose_the_sqlalchemy_driver():
    assert plain_postgres_url("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    assert plain_postgres_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_a_change_after_approval_is_shown_as_not_live(boardy):
    conversations, model = boardy
    model.replies += [say("Hello."), submit(), say("Approved."),
                      calls(("set_destination", {"url": "https://hooks.example.com/new",
                                                 "events": ["event.completed"]})),
                      say("Updated.")]
    send(conversations, "Hi")
    conversations.graph.update_state(boardy_graph.thread_config(USER, "c1"),
                                     {"draft": COMPLETE_DRAFT})
    send(conversations, "Submit it")
    approved = conversations.answer_approval(USER, "c1", True)
    assert approved["changes_pending"] is False

    changed = send(conversations, "Use https://hooks.example.com/new instead")

    assert changed["approved"] is False and changed["saved_version"] == 1
    assert changed["changes_pending"] is True

