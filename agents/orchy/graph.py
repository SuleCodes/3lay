"""Orchy's graph: wires the nodes together and decides where each event goes next.

    START -> load_inputs --+--> extract --+--> validate --+--> finish --> build_envelope --> END
                           |       ^      |       |       |
                           |       +------+       |       +--> extract
                           |    a document to     |   a document's schema check failed,
                           |    retry (transient  |   attempts left
                           |    error)            |
                           +--> finish            +--> finish  nothing left to validate
             no usable attachments

An event is one email with one or more documents, processed one after another.
Nodes do the work and record each document's status ("pending", "retry",
"extracted", "done", "failed"); the routing functions below read those
statuses and are the only place the flow is decided. They're plain code, not
a model: this is Obed's fixed-recipe mode.

Every run ends at finish, which sets the event's status from all its
documents ("completed" if any document is done, "failed" if none is), then
build_envelope, which builds what the client receives.
"""

from langgraph.graph import END, START, StateGraph

from orchy import nodes
from orchy.state import OrchyState


def any_document(state, status):
    return any(d["status"] == status for d in state.get("documents", []))


def route_after_load_inputs(state):
    """No usable attachments: straight to finish. Otherwise extract them."""
    return "finish" if state.get("status") == "failed" else "extract"


def route_after_extract(state):
    """Retry first (only the documents that need it), then validate what was extracted."""
    if any_document(state, "retry"):
        return "extract"
    if any_document(state, "extracted"):
        return "validate"
    return "finish"  # every document failed


def route_after_validate(state):
    """A document whose schema check failed with attempts left goes back to extract."""
    return "extract" if any_document(state, "retry") else "finish"


def build_graph():
    """Builds and compiles the graph. A function, so importing this module does no work."""
    builder = StateGraph(OrchyState)

    builder.add_node("load_inputs", nodes.load_inputs)
    builder.add_node("extract", nodes.extract)
    builder.add_node("validate", nodes.validate)
    builder.add_node("finish", nodes.finish)
    builder.add_node("build_envelope", nodes.build_envelope)

    builder.add_edge(START, "load_inputs")
    # The list after each routing function names every place it can send the
    # event: LangGraph checks them when compiling and uses them to draw the graph.
    builder.add_conditional_edges("load_inputs", route_after_load_inputs, ["extract", "finish"])
    builder.add_conditional_edges("extract", route_after_extract,
                                  ["extract", "validate", "finish"])
    builder.add_conditional_edges("validate", route_after_validate, ["extract", "finish"])
    builder.add_edge("finish", "build_envelope")
    builder.add_edge("build_envelope", END)

    return builder.compile()
