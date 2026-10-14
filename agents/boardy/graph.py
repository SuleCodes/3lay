"""Boardy's graph: the agent loop.

    START -> boardy --(did it ask for tools?)-- yes --> tools --+
               ^                                                |
               +------------------------------------------------+
               |
               no: it replied in words --> END (wait for the client's next message)

Unlike Orchy, no code decides the route: the model does. The routing function
(LangGraph's tools_condition) only looks at what the model asked for: tool
calls go to the tool node, which runs them and adds their results to the
conversation; a reply in words ends the turn. Then the model sees the results
and decides again: fix a mistake, call another tool, or answer the client.

Memory: compiled with a checkpointer, the state is saved after every step,
under the conversation's thread id. Each turn then sends only the client's new
message with the thread id (thread_config); LangGraph loads the rest. A thread
id is "<customer id>:<conversation id>": the customer's id (users.id), never
their email or username, which can change or not exist (a client may sign up
without email), and a conversation id, so one customer can have several
conversations (onboarding now, changing their configuration later).

Pausing: submit_draft calls interrupt, which stops the graph mid-step. The
result then has "__interrupt__" (the approval question), and the conversation
waits, saved, until it's resumed with Command(resume={"approved": ..., ...}).
"""

import operator
import os
from functools import cache
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from boardy.draft import empty_draft, merge_draft
from boardy.instructions import INSTRUCTIONS
from boardy.samples import samples_text
from boardy.tools import TOOLS
from obed.extract import create_model
from settings import load_settings


def merge_notes(current, new):
    return {**(current or {}), **(new or {})}


class BoardyState(MessagesState):
    """The conversation (messages, with the add_messages reducer from
    MessagesState), the customer it's for, and the draft being built.

    The draft has its own reducer, merge_draft: tools return changes, not whole
    drafts, so tools called together don't overwrite each other's changes.
    sample_notes and sample_tries have reducers for the same reason.
    """

    user_id: str    # the customer's id (users.id)
    username: str   # their 3lay address is <username>@in.3lay.live
    draft: Annotated[dict[str, Any], merge_draft]
    samples: list[dict[str, Any]]  # the client's sample documents (boardy/samples.py)
    sample_notes: Annotated[dict[str, str], merge_notes]  # read_sample's description, by id
    sample_tries: Annotated[int, operator.add]  # try_on_samples runs so far
    saved_version: int  # the draft version saved when the client approved


@cache
def boardy_model():
    """Boardy's model (AGENTS:BOARDY_MODEL), with the tools it may call. Created once."""
    load_settings()
    model = create_model(os.environ["BOARDY_MODEL"], os.environ["OBED_BASE_URL"],
                         os.environ["HF_TOKEN"])
    return model.bind_tools(TOOLS)


def boardy(state: BoardyState) -> dict:
    """One decision: reply to the client, or call tools."""
    system = SystemMessage(INSTRUCTIONS.format(
        username=state["username"], samples=samples_text(state.get("samples") or [])))
    reply = boardy_model().invoke([system, *state["messages"]])
    return {"messages": [reply]}


def build_graph(checkpointer=None):
    """Boardy's graph. With a checkpointer, conversations are remembered by thread id."""
    builder = StateGraph(BoardyState)
    builder.add_node("boardy", boardy)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "boardy")
    # tools_condition returns "tools" if the last message asks for tool calls, else END.
    builder.add_conditional_edges("boardy", tools_condition, ["tools", END])
    builder.add_edge("tools", "boardy")
    return builder.compile(checkpointer=checkpointer)


def thread_id(user_id, conversation_id):
    """The checkpointer's key for one conversation: "<customer id>:<conversation id>"."""
    for name, part in (("user_id", user_id), ("conversation_id", conversation_id)):
        if not isinstance(part, str) or not part.strip() or ":" in part:
            raise ValueError(f"{name} must be non-empty text without ':', got {part!r}")
    return f"{user_id}:{conversation_id}"


def thread_config(user_id, conversation_id):
    """What every invoke for this conversation passes, so LangGraph loads and saves its state."""
    return {"configurable": {"thread_id": thread_id(user_id, conversation_id)}}


def client_message(graph, config, text, *, user_id, username, samples=None):
    """The input for the client's next message.

    A new conversation also needs who it's for and an empty draft; after that
    they're in the saved state, and sending the draft again would overwrite it.
    samples, if given, replace the conversation's list: the chat sends the
    current list each turn, since the client can upload more at any time.
    """
    message = {"messages": [HumanMessage(text)]}
    if samples is not None:
        message["samples"] = list(samples)
    if graph.get_state(config).values:
        return message
    return {"samples": [], **message, "user_id": user_id, "username": username,
            "draft": empty_draft(), "sample_notes": {}, "sample_tries": 0}


def pending_question(result):
    """The approval question the conversation is paused on, or None."""
    interrupts = result.get("__interrupt__") or []
    return interrupts[0].value if interrupts else None
