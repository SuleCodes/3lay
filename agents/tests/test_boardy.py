"""Boardy: schema conventions, and the agent loop with its tools.

A scripted fake model stands in for the real one: each time Boardy's model
node runs, it gives the next scripted message (tool calls, or a reply in
words). So these tests check the loop, the tools and their checks, not the
model's judgement (that's stage B6).
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from boardy import graph as boardy_graph
from boardy.conventions import check_schema_conventions, schema_at
from boardy.draft import ADD_RULE, REMOVE_RULE, empty_draft, merge_draft, missing, to_configuration
from boardy.instructions import INSTRUCTIONS
from store.configurations import check_configuration

SCHEMA = {
    "$id": "rolepay-income-statement/1",
    "type": "object",
    "additionalProperties": False,
    "required": ["payment_date", "total_net", "lines"],
    "properties": {
        "payment_date": {"type": ["string", "null"], "description": "Date paid, YYYY-MM-DD."},
        "total_net": {"type": ["number", "null"], "description": "Total paid, if printed."},
        "lines": {
            "type": "array",
            "description": "One per job or payment.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gross", "net", "deductions"],
                "properties": {
                    "gross": {"type": "number", "description": "Fee before deductions."},
                    "net": {"type": "number", "description": "Amount paid for the line."},
                    "deductions": {
                        "type": "array",
                        "description": "Every amount taken off.",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["category", "amount"],
                            "properties": {
                                "category": {"type": "string", "description": "What it is."},
                                "amount": {"type": "number", "description": "How much."},
                            },
                        },
                    },
                },
            },
        },
    },
}

LINE_TOTALS = {"id": "line_totals_add_up", "type": "sum_equals", "scope": "lines[]",
               "terms": ["+gross", "-deductions[].amount"], "equals": "net",
               "on_fail": "needs_review", "message": "Line totals don't add up"}


def with_field(path, field):
    """SCHEMA with one top-level field added (and listed in required)."""
    schema = json.loads(json.dumps(SCHEMA))
    schema["properties"][path] = field
    schema["required"].append(path)
    return schema


# Schema conventions

def test_a_schema_following_the_conventions_passes():
    assert not check_schema_conventions(SCHEMA)


@pytest.mark.parametrize("schema, problem", [
    (with_field("issuer", {"type": "string"}), "issuer: needs a description"),
    (with_field("sort_code", {"type": "string", "description": "x"}), "personal identifiers"),
    (with_field("ni_number", {"type": "string", "description": "x"}), "personal identifiers"),
    (with_field("total_gross", {"type": "string", "description": "x"}), "must be a number"),
    (with_field("issued_date", {"type": "number", "description": "x"}), "a date must be text"),
    ({**SCHEMA, "$id": "no version"}, "$id"),
    ({**SCHEMA, "required": ["payment_date"]}, "list every field in required"),
    ({**SCHEMA, "additionalProperties": True}, "additionalProperties"),
    ({**SCHEMA, "type": "not-a-type"}, "Not a valid JSON Schema"),
])
def test_breaking_a_convention_is_reported(schema, problem):
    assert any(problem in p for p in check_schema_conventions(schema))


def test_fields_inside_lists_are_checked_too():
    schema = json.loads(json.dumps(SCHEMA))
    del schema["properties"]["lines"]["items"]["properties"]["deductions"]["items"][
        "properties"]["amount"]["description"]

    assert check_schema_conventions(schema) == [
        "lines[].deductions[].amount: needs a description (it's the instruction Obed reads)"]


def test_names_that_merely_contain_a_money_word_arent_money():
    for name in ("tax_code", "tax_year", "fee_label"):
        assert not check_schema_conventions(
            with_field(name, {"type": "string", "description": "x"}))


def test_schema_at_finds_paths_through_lists_and_filters():
    assert schema_at(SCHEMA, "total_net")["type"] == ["number", "null"]
    assert schema_at(SCHEMA, "lines[].deductions[category=fees].amount")["type"] == "number"
    assert schema_at(SCHEMA, "lines[].nope") is None
    assert schema_at(SCHEMA, "total_net[]") is None  # not a list


# The agent loop, with a scripted model

class ScriptedModel:
    """Gives the next scripted message each time it's called, and records what it saw."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def invoke(self, messages):
        self.seen.append(messages)
        return self.replies.pop(0)


def calls(*tool_calls):
    """The model asking for tools: (name, args) pairs."""
    return AIMessage("", tool_calls=[
        {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
        for i, (name, args) in enumerate(tool_calls)])


def say(text):
    return AIMessage(text)


@pytest.fixture
def boardy(monkeypatch):
    """Runs one client turn against a scripted model. Returns (final state, model)."""
    def run(*replies, draft=None, text="Hello"):
        model = ScriptedModel(*replies)
        monkeypatch.setattr(boardy_graph, "boardy_model", lambda: model)
        state = boardy_graph.build_graph().invoke({
            "username": "rolepay-agent", "draft": draft or empty_draft(),
            "messages": [HumanMessage(text)]})
        return state, model
    return run


def tool_results(state):
    return [m.content for m in state["messages"] if isinstance(m, ToolMessage)]


def test_a_reply_in_words_ends_the_turn(boardy):
    state, model = boardy(say("Hi! What does your business do?"))

    assert state["messages"][-1].content == "Hi! What does your business do?"
    assert len(model.seen) == 1


def test_tool_results_go_back_to_the_model_which_then_replies(boardy):
    state, model = boardy(
        calls(("set_document_type", {"name": "income_statement",
                                     "description": "An actor's pay statement.",
                                     "instructions": "Extract every payment line."})),
        say("Got it. Which fields do you need?"))

    assert state["draft"]["document_type"]["name"] == "income_statement"
    assert tool_results(state) == ["Saved the document type 'income_statement'."]
    # The second time the model ran, it could see the tool's result.
    assert isinstance(model.seen[1][-1], ToolMessage)
    assert state["messages"][-1].content == "Got it. Which fields do you need?"


def test_a_refused_schema_leaves_the_draft_unchanged_and_the_model_can_fix_it(boardy):
    broken = with_field("issuer", {"type": "string"})  # no description

    state, _ = boardy(calls(("set_schema", {"json_schema": broken})),
                      calls(("set_schema", {"json_schema": SCHEMA})),
                      say("Done."))

    first, second = tool_results(state)
    assert first.startswith("Not saved") and "issuer: needs a description" in first
    assert second.startswith("Saved the schema")
    assert state["draft"]["schema"] == SCHEMA


def test_a_rule_needs_the_schema_and_its_fields_to_exist(boardy):
    bad_path = {**LINE_TOTALS, "id": "nope", "equals": "net_pay"}

    state, _ = boardy(calls(("add_rule", {"rule": LINE_TOTALS})),
                      calls(("set_schema", {"json_schema": SCHEMA})),
                      calls(("add_rule", {"rule": bad_path})),
                      calls(("add_rule", {"rule": LINE_TOTALS})),
                      say("Done."))

    no_schema, _, missing_field, added = tool_results(state)
    assert "set the schema first" in no_schema
    assert "'net_pay' isn't a field in the schema (inside lines[])" in missing_field
    assert added == "Added the rule 'line_totals_add_up'."
    assert [r["id"] for r in state["draft"]["rules"]] == ["line_totals_add_up"]


def test_an_invalid_rule_is_refused_with_the_rule_checks_reasons(boardy):
    draft = {**empty_draft(), "schema": SCHEMA}

    state, _ = boardy(calls(("add_rule", {"rule": {"id": "x", "type": "average"}})),
                      say("Hmm."), draft=draft)

    assert "unknown type 'average'" in tool_results(state)[0]


def test_adding_a_rule_with_the_same_id_replaces_it(boardy):
    draft = {**empty_draft(), "schema": SCHEMA, "rules": [LINE_TOTALS]}
    stricter = {**LINE_TOTALS, "on_fail": "reject"}

    state, _ = boardy(calls(("add_rule", {"rule": stricter})), say("Ok."), draft=draft)

    assert tool_results(state) == ["Replaced the rule 'line_totals_add_up'."]
    assert state["draft"]["rules"] == [stricter]


@pytest.mark.parametrize("url, problem", [
    ("https://localhost/hook", "local or internal"),
    ("https://10.0.0.5/hook", "private or reserved"),
    ("https://192.168.1.10/hook", "private or reserved"),
    ("https://169.254.169.254/latest", "private or reserved"),  # cloud metadata address
    ("https://payroll.internal/hook", "local or internal"),
    ("http://hooks.rolepay.example/3lay", "url"),  # not https
])
def test_webhooks_must_go_to_a_public_https_server(boardy, url, problem):
    state, _ = boardy(calls(("set_destination", {"url": url, "events": ["event.completed"]})),
                      say("Ok."))

    result = tool_results(state)[0]
    assert result.startswith("Not saved") and problem in result
    assert state["draft"]["destinations"] == []


def test_a_webhook_gets_a_stable_id_from_the_client(boardy):
    state, _ = boardy(calls(("set_destination", {"url": "https://hooks.rolepay.example/3lay",
                                                 "events": ["event.completed",
                                                            "event.failed"]})),
                      say("Saved."))

    [destination] = state["draft"]["destinations"]
    assert destination["id"] == "dest_rolepay_agent_main"
    assert "secret" not in destination


def test_attempts_must_be_within_3lays_limits(boardy):
    state, _ = boardy(calls(("set_processing", {"max_attempts": 9})),
                      calls(("set_processing", {"max_attempts": 2})), say("Ok."))

    refused, saved = tool_results(state)
    assert "1 to 5" in refused and saved.startswith("Saved")
    assert state["draft"]["processing"] == {"max_attempts": 2}


def test_show_draft_lists_what_is_missing(boardy):
    state, _ = boardy(calls(("show_draft", {})), say("We still need a few things."))

    assert "Still missing: the document type" in tool_results(state)[0]


def test_a_whole_onboarding_produces_a_configuration_that_passes_3lays_check(boardy):
    state, _ = boardy(
        calls(("set_document_type", {"name": "income_statement",
                                     "description": "An actor's pay statement.",
                                     "instructions": "Copy values exactly as printed."}),
              ("set_schema", {"json_schema": SCHEMA})),
        calls(("add_rule", {"rule": LINE_TOTALS}),
              ("set_destination", {"url": "https://hooks.rolepay.example/3lay",
                                   "events": ["event.completed", "event.failed"]})),
        calls(("show_draft", {})),
        say("Your configuration is complete."))

    assert "Complete: it passes 3lay's configuration check." in tool_results(state)[-1]
    assert not missing(state["draft"])
    check_configuration(to_configuration(state["draft"]))


def test_the_instructions_name_the_client_and_include_the_capability_sheet():
    text = INSTRUCTIONS.format(username="rolepay-agent")

    assert "username: rolepay-agent" in text
    assert "Not available yet" in text
    assert "Never ask for passwords" in text


# The draft's reducer: tools called together don't overwrite each other

def test_two_rules_added_in_the_same_step_are_both_kept(boardy):
    draft = {**empty_draft(), "schema": SCHEMA}
    totals = {"id": "totals_match_lines", "type": "sum_equals", "terms": ["+lines[].net"],
              "equals": "total_net", "on_fail": "needs_review"}

    state, _ = boardy(calls(("add_rule", {"rule": LINE_TOTALS}), ("add_rule", {"rule": totals})),
                      say("Added both."), draft=draft)

    assert sorted(r["id"] for r in state["draft"]["rules"]) == [
        "line_totals_add_up", "totals_match_lines"]


def test_merge_draft_applies_changes_in_order():
    draft = merge_draft({}, empty_draft())
    draft = merge_draft(draft, {"schema": SCHEMA})
    draft = merge_draft(draft, {ADD_RULE: LINE_TOTALS})
    draft = merge_draft(draft, {ADD_RULE: {**LINE_TOTALS, "on_fail": "reject"}})  # replaces
    assert draft["schema"] == SCHEMA
    assert [r["on_fail"] for r in draft["rules"]] == ["reject"]
    assert merge_draft(draft, {REMOVE_RULE: "line_totals_add_up"})["rules"] == []
