"""Boardy behind an API: one conversation per thread, saved in Postgres.

The backend calls a Conversations object for each chat request. Every call
gets the customer's id from their session (never from the request), so the
thread id "<customer id>:<conversation id>" can only ever reach their own
conversations. What comes back is a view for the chat: the messages the
client sees (not tool calls or tool results), the approval question if the
conversation is paused on one, and its status.

A turn can fail part-way (e.g. Boardy's model provider is down). The client's
message is already saved by then, so the conversation is left "failed" and
retry() carries on from where it stopped, instead of the client sending the
message again.

Saved conversations hold everything the client said and what was read from
their samples, so they're encrypted (CONVERSATION_ENCRYPTION_KEY) and kept in
their own schema, "boardy", created by scripts/setup_conversations.py.
"""

import base64
import logging
import os
import threading
from collections import defaultdict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from boardy.draft import missing
from boardy.graph import build_graph, client_message, thread_config

log = logging.getLogger("3lay.boardy")

CHECKPOINT_SCHEMA = "boardy"

# What the client sees for each tool Boardy uses, so they can follow what it did.
STEP_LABELS = {
    "set_document_type": "Recorded the document type",
    "set_schema": "Recorded the fields to extract",
    "add_rule": "Added a check",
    "remove_rule": "Removed a check",
    "set_destination": "Recorded your webhook",
    "set_processing": "Recorded how many attempts to make",
    "show_draft": "Checked the setup so far",
    "submit_draft": "Asked for your approval",
    "read_sample": "Read a sample",
    "try_on_samples": "Tried the setup on your samples",
}


class ConversationBusy(Exception):
    """Another request is already running a turn of this conversation."""


class WrongState(Exception):
    """The request doesn't fit where the conversation is, e.g. a message while
    it waits for an approval answer. The message says what to do instead."""


class BoardyUnavailable(Exception):
    """The turn failed part-way; the conversation can be retried."""


def conversation_key():
    """The AES key that encrypts saved conversations: 32 bytes, base64 in the environment."""
    key = base64.b64decode(os.environ["CONVERSATION_ENCRYPTION_KEY"])
    if len(key) != 32:
        raise ValueError("CONVERSATION_ENCRYPTION_KEY must be 32 bytes, base64-encoded")
    return key


def plain_postgres_url(url):
    """psycopg wants postgresql://, not SQLAlchemy's postgresql+psycopg://."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def postgres_checkpointer(url):
    """LangGraph's Postgres checkpointer on a connection pool, in the boardy schema, encrypted.

    Uses Boardy's database role (ONBOARDING_DATABASE_URL). The tables must
    already exist (scripts/setup_conversations.py): this role can't create them.
    """
    # Imported here so the terminal and the tests don't need them.
    # pylint: disable=import-outside-toplevel
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    def use_boardy_schema(connection):
        connection.execute(f"set search_path to {CHECKPOINT_SCHEMA}")

    pool = ConnectionPool(
        plain_postgres_url(url), min_size=1, max_size=5, open=True,
        configure=use_boardy_schema,
        # What the checkpointer expects; no server-side prepared statements, so
        # it also works behind Supabase's transaction pooler.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None})
    serde = EncryptedSerializer.from_pycryptodome_aes(key=conversation_key())
    return PostgresSaver(pool, serde=serde)


def message_text(message):
    text = message.text
    return text if isinstance(text, str) else str(text)


def chat_messages(messages):
    """The conversation as the client sees it: their messages and Boardy's replies.

    Tool calls and results stay hidden; each reply lists the steps Boardy took
    before it, in plain words.
    """
    shown, steps = [], []
    for message in messages:
        if isinstance(message, HumanMessage):
            shown.append({"role": "client", "text": message_text(message), "steps": []})
        elif isinstance(message, AIMessage):
            steps += [STEP_LABELS.get(call["name"], call["name"]) for call in message.tool_calls]
            text = message_text(message).strip()
            if text:
                shown.append({"role": "boardy", "text": text, "steps": steps})
                steps = []
    return shown


class Conversations:
    """Runs Boardy turns for the API. One per process; safe across request threads."""

    def __init__(self, checkpointer):
        self.graph = build_graph(checkpointer=checkpointer)
        self._locks = defaultdict(threading.Lock)
        self._locks_lock = threading.Lock()

    def view(self, user_id, conversation_id):
        """Where the conversation is, for the chat."""
        snapshot = self.graph.get_state(thread_config(user_id, conversation_id))
        values = snapshot.values or {}
        question = next((i.value for i in snapshot.interrupts), None)
        approved = bool((values.get("draft") or {}).get("approved"))
        if question:
            status = "awaiting_approval"
        elif snapshot.next:
            status = "failed"  # stopped part-way through a turn
        else:
            status = "ready"
        return {
            "status": status,
            "messages": chat_messages(values.get("messages", [])),
            "approval": {"summary": question["summary"]} if question else None,
            "missing": missing(values["draft"]) if values.get("draft") else [],
            "approved": approved,
            "saved_version": values.get("saved_version"),
            # Approved earlier, then changed: the changes aren't live until approved
            # again. Shown by the chat itself, whatever Boardy says.
            "changes_pending": values.get("saved_version") is not None and not approved,
        }

    def send(self, user_id, username, conversation_id, text, samples):
        """The client's next message. samples: the conversation's current samples."""
        text = (text or "").strip()
        if not text:
            raise WrongState("Write a message first.")
        config = thread_config(user_id, conversation_id)
        status = self.view(user_id, conversation_id)["status"]
        if status == "awaiting_approval":
            raise WrongState("Boardy is waiting for your approval: approve or ask for changes.")
        if status == "failed":
            raise WrongState("The last turn didn't finish: retry it first.")
        graph_input = client_message(self.graph, config, text, user_id=user_id,
                                     username=username, samples=samples)
        return self._run(user_id, conversation_id, graph_input)

    def answer_approval(self, user_id, conversation_id, approved, comments=""):
        if self.view(user_id, conversation_id)["status"] != "awaiting_approval":
            raise WrongState("There's no approval question to answer.")
        answer = {"approved": bool(approved), "comments": (comments or "").strip()}
        return self._run(user_id, conversation_id, Command(resume=answer))

    def retry(self, user_id, conversation_id):
        """Carries on a turn that failed part-way, from its last saved step."""
        if self.view(user_id, conversation_id)["status"] != "failed":
            raise WrongState("There's nothing to retry.")
        return self._run(user_id, conversation_id, None)

    def _run(self, user_id, conversation_id, graph_input):
        config = thread_config(user_id, conversation_id)
        lock = self._lock(config["configurable"]["thread_id"])
        if not lock.acquire(blocking=False):
            raise ConversationBusy("Boardy is still answering your last message.")
        try:
            self.graph.invoke(graph_input, config)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # The model or a tool failed: what ran before is saved, so it can be retried.
            log.exception("Boardy turn failed for %s", config["configurable"]["thread_id"])
            raise BoardyUnavailable("Boardy couldn't finish that. Try again in a moment.") from exc
        finally:
            lock.release()
        return self.view(user_id, conversation_id)

    def _lock(self, thread_id):
        with self._locks_lock:
            return self._locks[thread_id]
