"""Orchy's state: the event's working memory as it moves through the graph.

Each node receives the whole state and returns only the fields it changes.
LangGraph merges those updates in: most fields are simply replaced, but a field
declared with a reducer (like `steps`) is combined with the old value instead.

One event is one email, which can carry several documents (attachments). Each
document has its own progress, result and checks; the event has the overall
status.
"""

import operator
from typing import Annotated, Any, TypedDict


class DocumentState(TypedDict, total=False):
    """One attachment. Its bytes aren't here: they're read from the email when needed."""

    index: int              # position among the email's attachments
    name: str
    content_type: str
    page_count: int

    # "pending" -> extract -> "extracted" -> validate -> "done"
    # "retry": extract again (transient error, or failed schema check, attempts left)
    # "failed": unsupported, unreadable, a permanent error, or out of attempts
    status: str
    attempts: int
    extraction: dict[str, Any] | None
    usage: dict[str, Any] | None
    # One {"name", "passed", "message"} per check; rule checks also have
    # "rule" and "on_fail". passed is None when a rule was skipped.
    checks: list[dict[str, Any]]
    error_code: str | None
    error_kind: str | None  # "transient" or "permanent"
    error_message: str | None


class OrchyState(TypedDict, total=False):
    """total=False: fields are filled in as the graph runs, so none are required up front."""

    # Set when the run starts (later: from the event and the client's configuration).
    event_id: str
    email_path: str           # later: a Blob Storage reference
    instructions: str
    output_schema: dict[str, Any]
    schema_name: str
    rules: list[dict[str, Any]]  # the client's rules (data), run by validate
    max_attempts: int          # per document

    # Set by load_inputs; each node after that returns the updated list.
    documents: list[DocumentState]

    # The event's outcome: "processing", then "completed" (at least one document
    # done) or "failed" (nothing usable). The error fields explain an event
    # that failed as a whole; each document carries its own errors.
    status: str
    error_code: str | None
    error_message: str | None

    # The step timeline. operator.add is the reducer: when a node returns
    # {"steps": [record, ...]}, LangGraph adds them to the old list rather than
    # replacing it, so every node's records are kept.
    steps: Annotated[list[dict[str, Any]], operator.add]
