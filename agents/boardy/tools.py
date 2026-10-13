"""Boardy's tools: each changes one piece of the draft and checks it straight away.

The model reads each tool's name, arguments and docstring to decide when and
how to call it, so the docstrings are written as instructions to Boardy.

A tool gets the current state injected (InjectedState: the model never sees
or fills that argument) and returns a Command: a state update, exactly like a
node's, carrying a change to the draft (merged in by the draft's reducer,
boardy/draft.py) and a ToolMessage with the result for the model. When a check
fails, the draft is left unchanged and the ToolMessage lists the problems, so
the model can fix them and call the tool again.
"""

import ipaddress
import json
from typing import Annotated
from urllib.parse import urlsplit

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from boardy.conventions import check_schema_conventions, schema_at
from boardy.draft import ADD_RULE, REMOVE_RULE, missing, to_configuration
from noti.destinations import EVENT_TYPES, check_destinations
from rules import check_rule_definitions
from store.configurations import check_configuration

MAX_ATTEMPTS_LIMIT = 5


def result(call_id, text, change=None):
    """The tool's answer to the model, and its change to the draft, if any."""
    update = {"messages": [ToolMessage(text, tool_call_id=call_id)]}
    if change is not None:
        update["draft"] = change
    return Command(update=update)


def problems_text(problems):
    return "Not saved. Fix these and call the tool again:\n- " + "\n- ".join(problems)


@tool
def set_document_type(name: str, description: str, instructions: str,
                      tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Record the kind of document the client sends.

    name: short, lower_case_with_underscores, e.g. "income_statement".
    description: what the document is, in a sentence.
    instructions: how to read it, in plain words for the extraction model,
    e.g. "Extract every payment line; copy values exactly as printed."
    """
    problems = []
    if not name or not name.replace("_", "").isalnum() or not name[0].isalpha() \
            or name != name.lower():
        problems.append("name must be lower_case_with_underscores, starting with a letter")
    if not instructions.strip():
        problems.append("instructions can't be empty")
    if problems:
        return result(tool_call_id, problems_text(problems))
    return result(tool_call_id, f"Saved the document type {name!r}.", {"document_type": {
        "name": name, "description": description.strip(), "instructions": instructions.strip()}})


@tool
def set_schema(json_schema: dict, state: Annotated[dict, InjectedState],
               tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Record the fields to extract, as a JSON Schema for ONE document.

    Follow 3lay's conventions, or the schema is refused:
    - "$id": "<client>-<document>/<version>", e.g. "acme-invoice/1"
    - "type": "object", "additionalProperties": false, and every field listed
      in "required"; a value that may not be on the document is
      ["<type>", "null"]
    - every field has a "description": it's the instruction for finding it
    - money is a number; dates are text
    - lists use "type": "array" with an "items" object following the same rules
    - never collect bank details, National Insurance numbers or passwords
    Calling it again replaces the whole schema; rules that no longer fit it are
    reported.
    """
    problems = check_schema_conventions(json_schema)
    if problems:
        return result(tool_call_id, problems_text(problems))
    stale = [r["id"] for r in state["draft"]["rules"] if rule_path_problems(r, json_schema)]
    note = (f" These rules refer to fields that no longer exist; update or remove them: "
            f"{', '.join(stale)}." if stale else "")
    fields = len((json_schema.get("properties") or {}))
    return result(tool_call_id, f"Saved the schema ({fields} top-level fields).{note}",
                  {"schema": json_schema})


def rule_paths(rule):
    """Every field path a rule refers to (relative to its scope)."""
    paths = [rule.get(key) for key in ("equals", "value", "percentage", "of", "left", "right")]
    paths += [term.lstrip("+-").rstrip("?") for term in rule.get("terms", [])]
    paths += list(rule.get("fields", []))
    return [p for p in paths if p]


def rule_path_problems(rule, schema):
    """Paths in the rule that don't exist in the schema."""
    base = schema
    if rule.get("scope"):
        base = schema_at(schema, rule["scope"])
        if base is None:
            return [f"scope {rule['scope']!r} isn't a list in the schema"]
    return [f"{path!r} isn't a field in the schema" + (f" (inside {rule['scope']})"
                                                         if rule.get("scope") else "")
            for path in rule_paths(rule) if schema_at(base, path) is None]


@tool
def add_rule(rule: dict, state: Annotated[dict, InjectedState],
             tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Add a check the extracted data should pass, or replace the rule with the same id.

    A rule is data: {"id", "type", "on_fail", "message", ...}. Types:
    - sum_equals: "terms" (each "+path" or "-path"; add "?" for "missing counts
      as 0") add up to "equals"; "tolerance" (default 0.01)
    - percentage_of: "value" is "percentage"% of "of"
    - compare: "left" "operator" ("=", "!=", "<", "<=", ">", ">=") "right"
    - date_within: every date in "fields" is between "days_before" days ago
      and "days_after" days ahead
    "scope": "lines[]" runs the rule once per item, with paths relative to it.
    Paths: "gross", "lines[].net", "deductions[category=agency_commission].amount".
    "on_fail": "needs_review" (a warning) or "reject" (a hard failure).
    Set the schema first: every path must exist in it.
    """
    draft = state["draft"]
    if not draft.get("schema"):
        return result(tool_call_id, "Not saved: set the schema first, so the rule's fields "
                                    "can be checked.")
    others = [r for r in draft["rules"] if r.get("id") != rule.get("id")]
    try:
        check_rule_definitions([*others, rule])
    except ValueError as exc:
        return result(tool_call_id, problems_text(str(exc).splitlines()[1:]))
    path_problems = rule_path_problems(rule, draft["schema"])
    if path_problems:
        return result(tool_call_id, problems_text(path_problems))
    replaced = len(others) < len(draft["rules"])
    return result(tool_call_id,
                  f"{'Replaced' if replaced else 'Added'} the rule {rule['id']!r}.",
                  {ADD_RULE: rule})


@tool
def remove_rule(rule_id: str, state: Annotated[dict, InjectedState],
                tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Remove a rule by its id."""
    rules = state["draft"]["rules"]
    kept = [r for r in rules if r.get("id") != rule_id]
    if len(kept) == len(rules):
        return result(tool_call_id, f"There's no rule {rule_id!r}. Rules: "
                                    f"{', '.join(r['id'] for r in rules) or 'none'}.")
    return result(tool_call_id, f"Removed the rule {rule_id!r}.", {REMOVE_RULE: rule_id})


def private_address_problem(url):
    """A reason the URL points inside a network rather than at the client's server, if it does."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return "the URL has no host"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        return f"{host} is a local or internal name"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None  # a public-looking name; it's resolved when delivering
    if not address.is_global:
        return f"{host} is a private or reserved address"
    return None


@tool
def set_destination(url: str, events: list[str], state: Annotated[dict, InjectedState],
                    tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Record the client's webhook: where each event's results are delivered.

    url: the client's https endpoint.
    events: which events it receives: "event.completed" (processed) and/or
    "event.failed" (couldn't be processed). Usually both.
    3lay creates the signing secret when the client approves; never ask for one.
    """
    destination = {"id": f"dest_{state['username'].replace('-', '_')}_main",
                   "type": "webhook", "url": url.strip(), "events": events}
    problems = []
    private = private_address_problem(destination["url"])
    if private:
        problems.append(f"url: {private}; webhooks must go to the client's public https server")
    try:
        check_destinations([destination])
    except ValueError as exc:
        problems += str(exc).splitlines()[1:]
    if problems:
        return result(tool_call_id, problems_text(problems + [
            f"events can be: {', '.join(EVENT_TYPES)}"]))
    return result(tool_call_id, f"Saved the webhook {destination['url']} "
                                f"({', '.join(events)}).",
                  {"destinations": [destination]})


@tool
def set_processing(max_attempts: int,
                   tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Record how many times a document is tried before giving up (1 to 5; default 3)."""
    if not 1 <= max_attempts <= MAX_ATTEMPTS_LIMIT:
        return result(tool_call_id, f"Not saved: max_attempts must be 1 to {MAX_ATTEMPTS_LIMIT}.")
    return result(tool_call_id, f"Saved: up to {max_attempts} attempts per document.",
                  {"processing": {"max_attempts": max_attempts}})


@tool
def show_draft(state: Annotated[dict, InjectedState],
               tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Show the configuration so far, what's still missing, and any problems with it."""
    draft = state["draft"]
    gaps = missing(draft)
    configuration = to_configuration(draft)
    lines = [json.dumps(configuration, indent=2)]
    if gaps:
        lines.append("Still missing: " + "; ".join(gaps) + ".")
    else:
        try:
            check_configuration(configuration)
            lines.append("Complete: it passes 3lay's configuration check.")
        except ValueError as exc:
            lines.append(str(exc))
    return result(tool_call_id, "\n".join(lines))


TOOLS = [set_document_type, set_schema, add_rule, remove_rule, set_destination,
         set_processing, show_draft]
