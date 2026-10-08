"""Orchy's graph: wires the nodes together and decides where each event goes next.

    START -> load_inputs --+--> extract --+--> validate --+--> END
                           |       ^      |       |       |
                           |       +------+       |       +--> extract  (schema failed,
                           |  transient error,    |                      attempts left)
                           |  attempts left       |
                           v                      v
                          fail <------------------+  permanent error, or out of attempts
                           |
                           +--> END

Nodes do the work and report what happened; the routing functions below are
the only place decisions are made (retry, carry on, or fail). They're plain
code, not a model: this is Obed's fixed-recipe mode.

A run ends at END either from validate (status "completed") or from fail
(status "failed"). How it ended is in the final state's status, not in which
route it took; the route is in steps.
"""

from langgraph.graph import END, START, StateGraph

from orchy import nodes
from orchy.state import OrchyState


def attempts_left(state):
    return state.get("attempts", 0) < state.get("max_attempts", nodes.DEFAULT_MAX_ATTEMPTS)


def route_after_load_inputs(state):
    """A document that won't open fails straight away; anything else gets extracted."""
    return "fail" if state.get("status") == "failed" else "extract"


def route_after_extract(state):
    """Carry on if extract worked; retry a transient error while attempts are left; else fail."""
    if state.get("error_kind") is None:
        return "validate"
    if state["error_kind"] == "transient" and attempts_left(state):
        return "extract"
    return "fail"  # a permanent error, or transient errors with no attempts left


def route_after_validate(state):
    """validate's step says "retry" when the schema check failed and attempts are left.

    Otherwise validate has already set status "completed" (with any failed
    checks recorded for Justice), so the run is finished.
    """
    return "extract" if state["steps"][-1]["outcome"] == "retry" else END


def build_graph():
    """Builds and compiles the graph. A function, so importing this module does no work."""
    builder = StateGraph(OrchyState)

    builder.add_node("load_inputs", nodes.load_inputs)
    builder.add_node("extract", nodes.extract)
    builder.add_node("validate", nodes.validate)
    builder.add_node("fail", nodes.fail)

    builder.add_edge(START, "load_inputs")

    builder.add_conditional_edges("load_inputs", route_after_load_inputs, ["extract", "fail"])
    builder.add_conditional_edges("extract", route_after_extract, ["validate", "extract", "fail"])
    builder.add_conditional_edges("validate", route_after_validate, ["extract", END])
    builder.add_edge("fail", END)

    return builder.compile()
