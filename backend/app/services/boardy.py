"""Boardy, the onboarding agent, as the API uses it.

Boardy lives in the agents package (agents/boardy/); the backend imports it
from there rather than copying it (tools/Planner.md, "Code"). Locally that's
the repo's agents/ folder; in the container it's /agents (see the Dockerfile).
AGENTS_DIR overrides either.

Boardy reads its own settings, App Configuration's AGENTS:* keys (its model,
the Hugging Face token, its database role, the conversation encryption key),
with the agents' settings loader. That loader puts them in the environment,
so it only runs once the backend's own settings are loaded (they're read once,
at import), and never overrides them.
"""

import logging
import os
import sys
import threading
from pathlib import Path

from fastapi import HTTPException, status

AGENTS_DIR = Path(os.environ.get("AGENTS_DIR") or Path(__file__).resolve().parents[3] / "agents")

logger = logging.getLogger("3lay.boardy")

_lock = threading.Lock()
_settings_loaded = False
_conversations = None


def _import_agents():
    if str(AGENTS_DIR) not in sys.path:
        sys.path.insert(0, str(AGENTS_DIR))


def _load_agent_settings():
    """The AGENTS:* settings, into the environment, once."""
    global _settings_loaded  # pylint: disable=global-statement
    _import_agents()
    with _lock:
        if not _settings_loaded:
            from settings import load_settings  # pylint: disable=import-outside-toplevel

            load_settings()
            _settings_loaded = True


def conversations():
    """The process's Conversations (agents/boardy/conversations.py), created on first use."""
    global _conversations  # pylint: disable=global-statement
    _load_agent_settings()
    with _lock:
        if _conversations is None:
            # pylint: disable=import-outside-toplevel
            from boardy.conversations import Conversations, postgres_checkpointer

            _conversations = Conversations(postgres_checkpointer(os.environ["ONBOARDING_DATABASE_URL"]))
        return _conversations


def run(action):
    """Runs a Boardy call, turning its errors into HTTP responses the chat can show."""
    _import_agents()
    from boardy.conversations import BoardyUnavailable, ConversationBusy, WrongState  # pylint: disable=import-outside-toplevel

    try:
        return action()
    except (WrongState, ConversationBusy) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BoardyUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def store_sample(samples, user_id, conversation_id, name, content_type, data):
    """Checks and stores an uploaded sample; returns it. A bad file is a 400 in words for the client."""
    _load_agent_settings()
    from boardy.samples import store_sample as store  # pylint: disable=import-outside-toplevel

    try:
        return store(samples, user_id, conversation_id, name, content_type, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Storage unreachable or not allowed (e.g. a missing role): the client
        # can't fix that, so a plain message for them and the cause in the log.
        logger.exception("Couldn't store a sample for %s/%s", user_id, conversation_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Couldn't store the sample right now. Try again later.") from exc


def max_sample_bytes():
    _import_agents()
    from boardy.samples import MAX_SAMPLE_BYTES  # pylint: disable=import-outside-toplevel

    return MAX_SAMPLE_BYTES


SWITCH_ON_FAILED = "Your setup is approved but couldn't be switched on yet. Try \"Switch on\" again in a moment."


def switch_on(user_id, version):
    """Activates an approved configuration version and creates secrets for new webhooks.

    Runs with the backend's own database connection: Boardy's role can only
    save drafts. Returns the new secrets ([{"destination_id", "url",
    "secret"}]) to show the client once; they're never logged or kept here.
    """
    _load_agent_settings()  # SECRETS_ENCRYPTION_KEY, for the new secrets
    # pylint: disable=import-outside-toplevel
    from app.database import engine
    from store.activation import switch_on as switch

    try:
        _, new_secrets = switch(engine, user_id, version)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Couldn't switch on version %s for %s", version, user_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SWITCH_ON_FAILED) from exc
    logger.info("Switched on version %s for %s (%d new secret(s))", version, user_id, len(new_secrets))
    return new_secrets


def rotate_secret(user_id, destination_id):
    """A new signing secret for one of the client's webhooks; returns it, to show once.

    The old secret stays valid for the store's grace period (both signatures
    are sent meanwhile), so the client can update their server without
    missing deliveries. Uses the agents' secret store with the backend's
    connection, like switch_on.
    """
    _load_agent_settings()  # SECRETS_ENCRYPTION_KEY
    # pylint: disable=import-outside-toplevel
    from app.database import engine
    from store.secrets import create_or_rotate_secret

    try:
        secret = create_or_rotate_secret(engine, user_id, destination_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Couldn't rotate the secret of %s for %s", destination_id, user_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Couldn't create a new secret right now. Try again later.") from exc
    logger.info("Rotated the secret of %s for %s", destination_id, user_id)
    return secret
