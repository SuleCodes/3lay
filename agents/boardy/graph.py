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
"""

import os
from functools import cache
from typing import Annotated, Any

from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from boardy.draft import merge_draft
from boardy.instructions import INSTRUCTIONS
from boardy.tools import TOOLS
from obed.extract import create_model
from settings import load_settings


class BoardyState(MessagesState):
    """The conversation (messages, with the add_messages reducer from
    MessagesState), the client it's for, and the draft being built.

    The draft has its own reducer, merge_draft: tools return changes, not whole
    drafts, so tools called together don't overwrite each other's changes.
    """

    username: str
    draft: Annotated[dict[str, Any], merge_draft]


@cache
def boardy_model():
    """Boardy's model (AGENTS:BOARDY_MODEL), with the tools it may call. Created once."""
    load_settings()
    model = create_model(os.environ["BOARDY_MODEL"], os.environ["OBED_BASE_URL"],
                         os.environ["HF_TOKEN"])
    return model.bind_tools(TOOLS)


def boardy(state: BoardyState) -> dict:
    """One decision: reply to the client, or call tools."""
    system = SystemMessage(INSTRUCTIONS.format(username=state["username"]))
    reply = boardy_model().invoke([system, *state["messages"]])
    return {"messages": [reply]}


def build_graph():
    builder = StateGraph(BoardyState)
    builder.add_node("boardy", boardy)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "boardy")
    # tools_condition returns "tools" if the last message asks for tool calls, else END.
    builder.add_conditional_edges("boardy", tools_condition, ["tools", END])
    builder.add_edge("tools", "boardy")
    return builder.compile()
