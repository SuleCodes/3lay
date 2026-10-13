"""Talk to Boardy in the terminal: you play the client, Boardy builds the draft.

    python scripts/talk_to_boardy.py rolepay-agent
    python scripts/talk_to_boardy.py rolepay-agent --quiet     (hide the tool calls)

Each tool call and its result is printed, so you can watch the agent loop:
Boardy decides, calls a tool, sees the result, decides again. Type "draft" to
see the draft so far, "quit" to stop (the draft is printed, not saved: saving
and approval come in stage B5). Uses AGENTS:BOARDY_MODEL.
"""

import argparse
import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from boardy.draft import empty_draft, to_configuration  # noqa: E402
from boardy.graph import build_graph  # noqa: E402
from settings import load_settings  # noqa: E402

GREETING = "Hi, I'm starting my 3lay onboarding."


def show_new_messages(messages, quiet):
    """Prints what happened this turn: tool calls, their results, then Boardy's reply."""
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if not quiet:
                    args = json.dumps(call["args"])
                    shown = args if len(args) <= 200 else args[:200] + "..."
                    print(f"  [tool] {call['name']}({shown})")
            if message.content:
                print(f"\nBoardy: {message.content}\n")
        elif isinstance(message, ToolMessage) and not quiet:
            first_line = str(message.content).splitlines()[0]
            print(f"  [result] {first_line}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", help="the client you're onboarding, e.g. rolepay-agent")
    parser.add_argument("--quiet", action="store_true", help="hide tool calls and results")
    args = parser.parse_args()

    load_settings()
    graph = build_graph()
    state = {"username": args.username, "draft": empty_draft(), "messages": []}
    text = GREETING
    print(f"You: {text}")

    while True:
        seen = len(state["messages"])
        # Without a checkpointer (stage B3), the whole state is passed in each
        # turn and the new state comes back.
        state = graph.invoke({**state, "messages": [*state["messages"], HumanMessage(text)]})
        show_new_messages(state["messages"][seen + 1:], args.quiet)

        text = input("You: ").strip()
        if text.lower() == "draft":
            print(json.dumps(to_configuration(state["draft"]), indent=2))
            text = input("You: ").strip()
        if text.lower() in ("quit", "exit"):
            print("Final draft (not saved):")
            print(json.dumps(to_configuration(state["draft"]), indent=2))
            return


if __name__ == "__main__":
    main()
