"""The envelope: what a client receives about an event. 3lay's contract with every client.

The same shape for every client; only each item's `data` follows the client's
own schema. See "Envelope" in tools/Planner.md (step 3 of the design). The shape
is pinned down by ENVELOPE_SCHEMA, and every envelope built is checked against
it in the tests, so a change that breaks the contract fails a test rather than
reaching a client.

Until Justice exists, each item's verdict comes from its checks (see verdict)
and confidence is null.
"""

DASHBOARD_URL = "https://app.3lay.live/events/{event_id}"
ENVELOPE_VERSION = 1

VERDICTS = ("accepted", "needs_review", "rejected")

_ERROR = {
    "type": ["object", "null"],
    "required": ["code", "message"],
    "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
    "additionalProperties": False,
}

_ITEM = {
    "type": "object",
    "required": ["source", "document_type", "schema", "verdict", "confidence",
                 "reasons", "data", "error"],
    "additionalProperties": False,
    "properties": {
        "source": {
            "type": "object",
            "required": ["attachment", "location", "forwarded_from"],
            "additionalProperties": False,
            "properties": {
                "attachment": {"type": "string"},
                "location": {"type": "string"},
                "forwarded_from": {"type": ["string", "null"]},
            },
        },
        "document_type": {"type": "string"},
        "schema": {
            "type": "object",
            "required": ["id", "version"],
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "version": {"type": ["integer", "null"]}},
        },
        "verdict": {"enum": list(VERDICTS)},
        "confidence": {"type": ["object", "null"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "data": {"type": ["object", "null"]},
        "error": _ERROR,
    },
}

ENVELOPE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "3lay event envelope",
    "type": "object",
    "required": ["envelope_version", "event_type", "event_id", "run", "case_ref",
                 "received_at", "finished_at", "sender", "status", "summary",
                 "config_version", "dashboard_url", "items", "analyses", "error"],
    "additionalProperties": False,
    "properties": {
        "envelope_version": {"const": ENVELOPE_VERSION},
        "event_type": {"enum": ["event.completed", "event.failed"]},
        "event_id": {"type": "string"},
        "run": {"type": "integer", "minimum": 1},
        "case_ref": {"type": "string"},
        "received_at": {"type": "string", "format": "date-time"},
        "finished_at": {"type": "string", "format": "date-time"},
        "sender": {"type": ["string", "null"]},
        "status": {"enum": ["completed", "failed"]},
        "summary": {
            "type": "object",
            "required": list(VERDICTS),
            "additionalProperties": False,
            "properties": {v: {"type": "integer", "minimum": 0} for v in VERDICTS},
        },
        "config_version": {"type": "integer"},
        "dashboard_url": {"type": "string"},
        "items": {"type": "array", "items": _ITEM},
        "analyses": {"type": "array"},
        "error": _ERROR,
    },
}


def failed_checks(document):
    return [c for c in document.get("checks", []) if c["passed"] is False]


def verdict(document):
    """accepted, needs_review or rejected, from the document's outcome and checks.

    - the document couldn't be processed (unsupported, unreadable, out of attempts): rejected
    - a rule with on_fail "reject" failed: rejected
    - the schema check, or a rule with on_fail "needs_review", failed: needs_review
    - otherwise: accepted. Skipped rules don't count against a document.
    """
    if document["status"] == "failed":
        return "rejected"
    failed = failed_checks(document)
    if any(c.get("on_fail") == "reject" for c in failed):
        return "rejected"
    return "needs_review" if failed else "accepted"


def reasons(document):
    """Why the verdict isn't "accepted", in words a client can act on."""
    if document["status"] == "failed":
        return [document.get("error_message") or "The document couldn't be processed."]
    return [f"{c['name']}: {c['message']}" if c.get("message") else c["name"]
            for c in failed_checks(document)]


def schema_reference(output_schema, fallback_name):
    """{"id", "version"} from the schema's $id, e.g. "rolepay-income-statement/1"."""
    schema_id = (output_schema or {}).get("$id") or fallback_name
    name, _, version = schema_id.rpartition("/")
    if name and version.isdigit():
        return {"id": name, "version": int(version)}
    return {"id": schema_id, "version": None}


def item(document, document_type, schema):
    rejected_unprocessed = document["status"] == "failed"
    return {
        "source": {
            "attachment": document["name"],
            "location": document.get("location", str(document["index"])),
            "forwarded_from": document.get("forwarded_from"),
        },
        "document_type": document_type,
        "schema": schema,
        "verdict": verdict(document),
        "confidence": None,  # Justice adds this later
        "reasons": reasons(document),
        "data": None if rejected_unprocessed else document.get("extraction"),
        "error": ({"code": document.get("error_code") or "processing_error",
                   "message": document.get("error_message") or ""}
                  if rejected_unprocessed else None),
    }


def build_envelope(state, finished_at):
    """The envelope for an event, from Orchy's final state."""
    schema = schema_reference(state.get("output_schema"), state.get("schema_name", "unknown"))
    document_type = state.get("document_type") or state.get("schema_name", "document")
    items = [item(d, document_type, schema) for d in state.get("documents", [])]
    failed = state.get("status") == "failed"
    event_id = state["event_id"]
    return {
        "envelope_version": ENVELOPE_VERSION,
        "event_type": "event.failed" if failed else "event.completed",
        "event_id": event_id,
        "run": state.get("run", 1),
        "case_ref": state.get("case_ref") or event_id,
        "received_at": state.get("received_at") or finished_at,
        "finished_at": finished_at,
        "sender": state.get("sender"),
        "status": "failed" if failed else "completed",
        "summary": {v: sum(1 for i in items if i["verdict"] == v) for v in VERDICTS},
        "config_version": state.get("config_version", 1),
        "dashboard_url": DASHBOARD_URL.format(event_id=event_id),
        "items": items,
        "analyses": [],
        "error": ({"code": state.get("error_code") or "processing_error",
                   "message": state.get("error_message") or ""} if failed else None),
    }
