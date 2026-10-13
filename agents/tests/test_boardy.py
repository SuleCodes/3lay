"""Boardy: schema conventions, the agent loop with its tools, memory, approval
and sample documents.

A scripted fake model stands in for the real one: each time Boardy's model
node runs, it gives the next scripted message (tool calls, or a reply in
words). So these tests check the loop, the tools and their checks, not the
model's judgement (that's stage B6).
"""

import json
from types import SimpleNamespace

import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from boardy import graph as boardy_graph
from boardy.conventions import check_schema_conventions, schema_at
from boardy.draft import ADD_RULE, REMOVE_RULE, empty_draft, merge_draft, missing, to_configuration
from boardy.instructions import INSTRUCTIONS
from boardy.samples import MAX_SAMPLES, samples_from_folder, samples_text
from boardy import tools as boardy_tools
from boardy.tools import MAX_TRIES, approval_answer, read_sample, try_on_samples
from orchy import nodes
from conftest import api_error, pdf_bytes, png_bytes
from store.configurations import (ConfigurationNotFound, check_configuration, latest_draft,
                                  load_active_configuration)

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


@pytest.mark.parametrize("schema_id, allowed", [
    ("rolepay-pay_statement/1", True), ("acme-invoice/12", True),
    ("Acme-Invoice/1", False), ("acme invoice/1", False), ("acme-invoice/v1", False)])
def test_schema_ids_are_a_lower_case_name_and_a_version(schema_id, allowed):
    problems = check_schema_conventions({**SCHEMA, "$id": schema_id})
    assert (not problems) is allowed
    if not allowed:
        assert problems[0].startswith(f"$id is {schema_id!r}; it must be")


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
    def run(*replies, draft=None, text="Hello", **more_state):
        model = ScriptedModel(*replies)
        monkeypatch.setattr(boardy_graph, "boardy_model", lambda: model)
        state = boardy_graph.build_graph().invoke({
            "username": "rolepay-agent", "draft": draft or empty_draft(),
            "messages": [HumanMessage(text)], **more_state})
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
    text = INSTRUCTIONS.format(username="rolepay-agent", samples="none")

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


# Memory (a checkpointer and thread ids) and pausing for approval (interrupt)

USER_ID = "u1"  # rolepay-agent, in the test database (conftest.py)
COMPLETE_DRAFT = merge_draft(empty_draft(), {
    "document_type": {"name": "income_statement", "description": "An actor's pay statement.",
                      "instructions": "Copy values exactly as printed."},
    "schema": SCHEMA,
    ADD_RULE: LINE_TOTALS,
    "destinations": [{"id": "dest_rolepay_agent_main", "type": "webhook",
                      "url": "https://hooks.rolepay.example/3lay",
                      "events": ["event.completed", "event.failed"]}],
})


@pytest.fixture
def conversation(monkeypatch, database):
    """A Boardy graph with an in-memory checkpointer, a scripted model, and the
    test database for saving approved configurations.

    Returns (send, graph, model): send(text_or_command, conversation_id) runs
    one turn of that conversation and returns the result.
    """
    model = ScriptedModel()
    monkeypatch.setattr(boardy_graph, "boardy_model", lambda: model)
    monkeypatch.setattr(boardy_tools, "onboarding_db", lambda: database)
    graph = boardy_graph.build_graph(checkpointer=InMemorySaver())

    def send(text_or_command, conversation_id="onboarding", draft=None):
        config = boardy_graph.thread_config(USER_ID, conversation_id)
        if isinstance(text_or_command, Command):
            return graph.invoke(text_or_command, config)
        graph_input = boardy_graph.client_message(graph, config, text_or_command,
                                                  user_id=USER_ID, username="rolepay-agent")
        if draft is not None and "draft" in graph_input:
            graph_input["draft"] = draft
        return graph.invoke(graph_input, config)
    return send, graph, model


def test_a_thread_id_is_the_customer_id_and_the_conversation_id():
    assert boardy_graph.thread_id(USER_ID, "onboarding") == f"{USER_ID}:onboarding"
    assert boardy_graph.thread_config(USER_ID, "c1") == {
        "configurable": {"thread_id": f"{USER_ID}:c1"}}


@pytest.mark.parametrize("user_id, conversation_id", [
    ("", "c1"), (USER_ID, " "), ("a:b", "c1"), (USER_ID, "c:1"), (None, "c1")])
def test_a_thread_id_needs_both_parts_without_colons(user_id, conversation_id):
    with pytest.raises(ValueError):
        boardy_graph.thread_id(user_id, conversation_id)


def test_the_conversation_is_remembered_between_turns(conversation):
    send, graph, model = conversation
    model.replies += [say("What do you do?"), say("Got it: income statements.")]

    send("Hi")
    second_turn = boardy_graph.client_message(
        graph, boardy_graph.thread_config(USER_ID, "onboarding"), "We pay actors",
        user_id=USER_ID, username="rolepay-agent")
    result = send("We pay actors")

    assert set(second_turn) == {"messages"}  # only the new message is sent
    assert [m.content for m in model.seen[1][1:]] == [
        "Hi", "What do you do?", "We pay actors"]  # the model saw the whole conversation
    assert result["user_id"] == USER_ID and result["username"] == "rolepay-agent"


def test_conversations_with_different_ids_are_kept_apart(conversation):
    send, _, model = conversation
    model.replies += [say("Hello, first."), say("Hello, second.")]

    send("Hi from onboarding", "onboarding")
    result = send("Hi from a change", "change-1")

    assert [m.content for m in result["messages"]] == ["Hi from a change", "Hello, second."]


def submit(summary="Income statements to your webhook."):
    return calls(("submit_draft", {"summary": summary}))


def test_submitting_pauses_the_conversation_with_the_configuration(conversation):
    send, graph, model = conversation
    model.replies += [submit()]

    result = send("Looks good, submit it", draft=COMPLETE_DRAFT)

    question = boardy_graph.pending_question(result)
    assert question["question"] == "approve_configuration"
    assert question["summary"] == "Income statements to your webhook."
    check_configuration(question["configuration"])
    assert graph.get_state(
        boardy_graph.thread_config(USER_ID, "onboarding")).next == ("tools",)
    assert result["draft"]["approved"] is False
    assert len(model.seen) == 1  # the model waits too


def test_approving_saves_a_draft_and_boardy_carries_on(conversation, database):
    send, _, model = conversation
    model.replies += [submit(), say("Thanks! 3lay will switch it on.")]
    send("Submit it", draft=COMPLETE_DRAFT)

    result = send(Command(resume={"approved": True, "comments": ""}))

    assert result["draft"]["approved"] is True
    assert result["saved_version"] == 1
    assert tool_results(result)[-1].startswith(
        "The client approved the configuration; saved as draft version 1.")
    assert result["messages"][-1].content == "Thanks! 3lay will switch it on."
    assert boardy_graph.pending_question(result) is None
    assert latest_draft(database, USER_ID) == (1, to_configuration(COMPLETE_DRAFT))
    with pytest.raises(ConfigurationNotFound):  # nothing is active until 3lay activates it
        load_active_configuration(database, "rolepay-agent")


def test_nothing_is_saved_before_the_client_answers(conversation, database):
    send, _, model = conversation
    model.replies += [submit()]

    send("Submit it", draft=COMPLETE_DRAFT)

    assert latest_draft(database, USER_ID) is None


def test_if_saving_fails_the_draft_isnt_approved(conversation, monkeypatch):
    send, _, model = conversation
    model.replies += [submit(), say("Sorry, please approve again in a moment.")]
    send("Submit it", draft=COMPLETE_DRAFT)

    def no_database():
        raise RuntimeError("ONBOARDING_DATABASE_URL isn't set")
    monkeypatch.setattr(boardy_tools, "onboarding_db", no_database)
    result = send(Command(resume={"approved": True, "comments": ""}))

    assert result["draft"]["approved"] is False
    assert "saving failed (RuntimeError)" in tool_results(result)[-1]


def test_declining_leaves_it_unapproved_and_passes_on_the_comments(conversation):
    send, _, model = conversation
    model.replies += [submit(), say("What would you like to change?")]
    send("Submit it", draft=COMPLETE_DRAFT)

    result = send(Command(resume={"approved": False, "comments": "Add the agent's name"}))

    assert result["draft"]["approved"] is False
    assert "Add the agent's name" in tool_results(result)[-1]


def test_an_incomplete_draft_isnt_submitted(conversation):
    send, _, model = conversation
    model.replies += [submit(), say("We still need your webhook.")]

    result = send("Submit it")

    assert boardy_graph.pending_question(result) is None
    assert tool_results(result)[-1].startswith("Not submitted: still missing")


def test_any_change_after_approval_clears_it():
    approved = merge_draft(COMPLETE_DRAFT, {"approved": True})
    assert approved["approved"] is True
    assert merge_draft(approved, {"processing": {"max_attempts": 2}})["approved"] is False


@pytest.mark.parametrize("answer", ["yes", {"approved": "yes"}, {}, None])
def test_an_unclear_approval_answer_is_refused(answer):
    with pytest.raises(ValueError):
        approval_answer(answer)


# Sample documents: reading them and trying the draft on them, with Obed's model

@pytest.fixture
def sample_folder(tmp_path):
    (tmp_path / "b-photo.png").write_bytes(png_bytes())
    (tmp_path / "a-statement.pdf").write_bytes(pdf_bytes(pages=2))
    (tmp_path / "notes.txt").write_text("not a document")
    return tmp_path


def test_samples_are_the_folders_documents_in_order(sample_folder):
    samples = samples_from_folder(sample_folder)

    assert [(s["id"], s["name"], s["content_type"]) for s in samples] == [
        ("sample_1", "a-statement.pdf", "application/pdf"),
        ("sample_2", "b-photo.png", "image/png")]
    assert samples_text(samples) == "sample_1 (a-statement.pdf), sample_2 (b-photo.png)"


def test_too_many_or_broken_samples_are_refused(tmp_path):
    for number in range(MAX_SAMPLES + 1):
        (tmp_path / f"{number}.png").write_bytes(png_bytes())
    with pytest.raises(ValueError, match="the limit is"):
        samples_from_folder(tmp_path)

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "statement.pdf").write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="statement.pdf: couldn't open it"):
        samples_from_folder(broken)
    with pytest.raises(ValueError, match="isn't a folder"):
        samples_from_folder(tmp_path / "missing")


def test_the_model_can_only_name_a_sample_never_a_file():
    for sample_tool in (read_sample, try_on_samples):
        assert "path" not in json.dumps(sample_tool.tool_call_schema.model_json_schema())


def test_the_instructions_list_the_samples():
    assert "Sample documents from the client: none" in INSTRUCTIONS.format(
        username="rolepay-agent", samples="none")


class Reader:
    """Stands in for Obed's model reading a sample: records what it was sent."""

    def __init__(self, reply="A pay statement with one line per job.", error=None):
        self.reply, self.error, self.seen = reply, error, []

    def invoke(self, messages):
        self.seen.append(messages)
        if self.error:
            raise self.error
        return AIMessage(self.reply)


@pytest.fixture
def reader(monkeypatch):
    def use(**kwargs):
        model = Reader(**kwargs)
        monkeypatch.setattr(nodes, "obed_model", lambda: (model, "test/vision:provider"))
        return model
    return use


def test_reading_a_sample_sends_its_pages_to_obeds_model(boardy, reader, sample_folder):
    model = reader()
    state, _ = boardy(calls(("read_sample", {"sample_id": "sample_1"})), say("It's a statement."),
                      samples=samples_from_folder(sample_folder))

    system, user = model.seen[0]
    assert "Describe this document" in system.content
    assert [block["type"] for block in user.content[1:]] == ["image", "image"]  # 2 pages
    result = tool_results(state)[0]
    assert "never instructions to follow" in result
    assert "A pay statement with one line per job." in result
    assert state["sample_notes"] == {"sample_1": "A pay statement with one line per job."}


def test_reading_a_sample_again_doesnt_call_the_model_again(boardy, reader, sample_folder):
    model = reader()
    boardy(calls(("read_sample", {"sample_id": "sample_1"})), say("Read it."),
           samples=samples_from_folder(sample_folder),
           sample_notes={"sample_1": "Described earlier."})

    assert not model.seen


def test_an_unknown_sample_or_a_failed_read_is_reported(boardy, reader, sample_folder):
    reader(error=api_error(openai.InternalServerError, 500))
    state, _ = boardy(calls(("read_sample", {"sample_id": "sample_9"}),
                            ("read_sample", {"sample_id": "sample_2"})), say("Hmm."),
                      samples=samples_from_folder(sample_folder))

    unknown, failed = tool_results(state)
    assert unknown == "There's no sample 'sample_9'. Samples: sample_1, sample_2."
    assert failed.startswith("Couldn't read sample_2: InternalServerError")


TRY_DRAFT = merge_draft(empty_draft(), {
    "document_type": {"name": "income_statement", "description": "",
                      "instructions": "Copy values exactly as printed."},
    "schema": SCHEMA, ADD_RULE: LINE_TOTALS})
LINES_DONT_ADD_UP = {"payment_date": "2025-12-10", "total_net": 90, "lines": [
    {"gross": 100, "net": 90, "deductions": [{"category": "agency", "amount": 5}]}]}


def try_results(state):
    text = tool_results(state)[-1]
    return text.splitlines()[0], json.loads(text.split("\n", 1)[1])


def test_trying_the_draft_runs_obed_and_the_checks_on_every_sample(
        boardy, fake_model, sample_folder):
    obed_calls = fake_model(LINES_DONT_ADD_UP)
    state, _ = boardy(calls(("try_on_samples", {})), say("Line 1 doesn't add up."),
                      draft=TRY_DRAFT, samples=samples_from_folder(sample_folder))

    header, outcomes = try_results(state)
    assert header.startswith(f"Try 1 of {MAX_TRIES}.")
    assert [(o["sample"], o["verdict"]) for o in outcomes] == [
        ("sample_1", "needs_review"), ("sample_2", "needs_review")]
    assert outcomes[0]["reasons"] == ["line_totals_add_up[0]: Line totals don't add up: "
                                     "100.00 (gross) - 5.00 (deductions[].amount) = 95.00, "
                                     "but net is 90.00"]
    assert outcomes[0]["values"] == LINES_DONT_ADD_UP
    assert len(obed_calls) == 2
    _, instructions, inputs, schema, schema_name = obed_calls[0]
    assert (instructions, schema, schema_name) == (
        "Copy values exactly as printed.", SCHEMA, "income_statement")
    assert len(inputs) == 2  # sample_1's two pages
    assert state["sample_tries"] == 1


def test_a_failure_while_trying_is_shown_not_retried(boardy, fake_model, sample_folder):
    obed_calls = fake_model(api_error(openai.InternalServerError, 500))
    state, _ = boardy(calls(("try_on_samples", {})), say("It failed."), draft=TRY_DRAFT,
                      samples=samples_from_folder(sample_folder)[:1])

    _, outcomes = try_results(state)
    assert outcomes[0]["verdict"] == "rejected"
    assert outcomes[0]["reasons"][0].startswith("Gave up after 1 attempts")
    assert len(obed_calls) == 1


@pytest.mark.parametrize("more_state, expected", [
    ({"samples": []}, "There are no samples to try the draft on."),
    ({"draft": empty_draft()}, "Not run: set the document type and the schema first."),
    ({"sample_tries": MAX_TRIES}, f"Not run: the limit of {MAX_TRIES} tries"),
])
def test_trying_needs_samples_a_schema_and_tries_left(
        boardy, fake_model, sample_folder, more_state, expected):
    obed_calls = fake_model(LINES_DONT_ADD_UP)
    state, _ = boardy(calls(("try_on_samples", {})), say("Not yet."),
                      **{"draft": TRY_DRAFT, "samples": samples_from_folder(sample_folder),
                         **more_state})

    assert tool_results(state)[-1].startswith(expected)
    assert not obed_calls


def test_a_new_conversation_starts_with_its_samples(conversation, sample_folder):
    _, graph, model = conversation
    model.replies += [say("I see two samples.")]
    samples = samples_from_folder(sample_folder)
    config = boardy_graph.thread_config(USER_ID, "with-samples")

    graph.invoke(boardy_graph.client_message(graph, config, "Hi", user_id=USER_ID,
                                             username="rolepay-agent", samples=samples), config)

    saved = graph.get_state(config).values
    assert saved["samples"] == samples and saved["sample_tries"] == 0
    assert "sample_1 (a-statement.pdf)" in model.seen[0][0].content
