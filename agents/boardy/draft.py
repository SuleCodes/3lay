"""The draft configuration Boardy builds, one piece at a time, during a conversation.

It's kept in Boardy's state (not the database) until it's submitted, and
turned into a client configuration (store/configurations.py) by
to_configuration. missing() says what Boardy still has to find out.

Tools never return a whole new draft: they return a change ("set the schema
to this", "add this rule"), and merge_draft, the draft's reducer, applies the
changes one after another. That's what lets the model call several tools at
once: they run in parallel, and without the reducer each would return a whole
draft built from the same old one, overwriting the others' changes.

"approved" is set when the client approves the draft (submit_draft), and any
other change clears it: the client approves exactly what they were shown.
"""

from store.configurations import DEFAULT_MAX_ATTEMPTS

# Changes a tool can return, besides replacing one of the draft's own pieces.
ADD_RULE = "add_rule"        # value: a rule; replaces any rule with the same id
REMOVE_RULE = "remove_rule"  # value: a rule id
APPROVED = "approved"        # value: True once the client approves this draft


def merge_draft(current, change):
    """The draft's reducer: applies one tool's change to the current draft."""
    draft = dict(current or {})
    for key, value in (change or {}).items():
        if key == ADD_RULE:
            draft["rules"] = [r for r in draft.get("rules", []) if r["id"] != value["id"]] + [value]
        elif key == REMOVE_RULE:
            draft["rules"] = [r for r in draft.get("rules", []) if r["id"] != value]
        else:
            draft[key] = value
        if key != APPROVED:
            draft[APPROVED] = False
    return draft


def empty_draft():
    return {
        "document_type": None,   # {"name", "description", "instructions"}
        "schema": None,          # the JSON Schema Obed fills in
        "rules": [],
        "destinations": [],
        "processing": {"max_attempts": DEFAULT_MAX_ATTEMPTS},
        APPROVED: False,
    }


def missing(draft):
    """What's still needed before the draft is a complete configuration."""
    gaps = []
    if not draft.get("document_type"):
        gaps.append("the document type (name, description, reading instructions)")
    if not draft.get("schema"):
        gaps.append("the fields to extract (the schema)")
    if not draft.get("destinations"):
        gaps.append("where results are delivered (a webhook)")
    return gaps


def to_configuration(draft):
    """The client configuration this draft describes (check it with check_configuration)."""
    document_type = draft.get("document_type") or {}
    return {
        "processing": dict(draft.get("processing") or {}),
        "document_types": [{
            "name": document_type.get("name"),
            "description": document_type.get("description", ""),
            "instructions": document_type.get("instructions"),
            "schema": draft.get("schema"),
            "rules": list(draft.get("rules", [])),
        }],
        "analyses": [],
        "destinations": list(draft.get("destinations", [])),
    }
