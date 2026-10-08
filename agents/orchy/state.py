"""Orchy's state: the event's working memory as it moves through the graph.

Each node receives the whole state and returns only the fields it changes.
LangGraph merges those updates in: most fields are simply replaced, but a field
declared with a reducer (like `steps`) is combined with the old value instead.
"""

import operator
from typing import Annotated, Any, TypedDict


class OrchyState(TypedDict, total=False):
    """total=False: fields are filled in as the graph runs, so none are required up front."""

    # Set when the run starts (later: from the event and the client's configuration).
    event_id: str
    document_path: str
    instructions: str
    output_schema: dict[str, Any]
    schema_name: str
    max_attempts: int

    # Set by load_inputs.
    page_count: int

    # Set by extract.
    attempts: int
    extraction: dict[str, Any] | None
    usage: dict[str, Any] | None

    # Set by validate: one {"name", "passed", "message"} per check.
    checks: list[dict[str, Any]]

    # Outcome. status is "processing", "completed" or "failed"; error_kind is
    # "transient" or "permanent", which the routing uses to decide on a retry.
    status: str
    error_code: str | None
    error_kind: str | None
    error_message: str | None

    # The step timeline. operator.add is the reducer: when a node returns
    # {"steps": [record]}, LangGraph does old_steps + [record] rather than
    # replacing the list, so every node's record is kept.
    steps: Annotated[list[dict[str, Any]], operator.add]
