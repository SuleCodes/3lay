"""Talk to Boardy in the terminal: you play the client, Boardy builds the draft.

    python scripts/talk_to_boardy.py rolepay-agent
    python scripts/talk_to_boardy.py rolepay-agent --quiet     (hide the tool calls)
    python scripts/talk_to_boardy.py rolepay-agent --conversation onboarding-2
    python scripts/talk_to_boardy.py rolepay-agent --samples tmp/samples/invented

The customer's id is looked up from their username (users.id, through
AGENTS:DATABASE_URL), and the conversation is kept by a checkpointer under the
thread id "<customer id>:<conversation id>": each turn sends only your new
message. The checkpointer here is in memory, so the conversation lasts as long
as the script; the backend will keep it in Postgres (stage B7).

--samples gives Boardy a folder of sample documents (PDF, JPEG, PNG; up to 5)
to read and try the draft on, with Obed's model (AGENTS:OBED_MODEL). Use
invented documents: samples are sent to the model provider.

Each tool call and its result is printed, so you can watch the agent loop.
When Boardy asks for approval (submit_draft), the conversation pauses until you
answer yes or no; nothing is saved yet (stage B5). Type "draft" to see the
draft so far, "quit" to stop. Uses AGENTS:BOARDY_MODEL.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from boardy.draft import to_configuration  # noqa: E402
from boardy.graph import (build_graph, client_message, pending_question,  # noqa: E402
                          thread_config)
from boardy.samples import samples_from_folder  # noqa: E402
from settings import load_settings  # noqa: E402
from store.configurations import user_id_for  # noqa: E402
from store.db import engine  # noqa: E402

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


def ask_for_approval(question):
    """Shows the approval question and returns the client's answer, to resume with."""
    print("\n=== Boardy asks for your approval ===")
    print(question["summary"])
    answer = input("Approve this configuration? (yes/no): ").strip().lower()
    comments = input("Any comments? ").strip()
    return {"approved": answer in ("y", "yes"), "comments": comments}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", help="the client you're onboarding, e.g. rolepay-agent")
    parser.add_argument("--conversation", help="the conversation id (default: a new one)")
    parser.add_argument("--samples", help="a folder of sample documents (up to 5)")
    parser.add_argument("--quiet", action="store_true", help="hide tool calls and results")
    args = parser.parse_args()
    try:
        samples = samples_from_folder(args.samples) if args.samples else []
    except ValueError as exc:
        parser.error(str(exc))

    load_settings()
    with engine().connect() as connection:
        user_id = user_id_for(connection, args.username)
    graph = build_graph(checkpointer=InMemorySaver())
    config = thread_config(user_id, args.conversation or uuid.uuid4().hex[:12])
    print(f"Thread {config['configurable']['thread_id']}")
    for sample in samples:
        print(f"Sample {sample['id']}: {sample['name']}")

    text = GREETING
    print(f"You: {text}")
    graph_input = client_message(graph, config, text, user_id=user_id, username=args.username,
                                 samples=samples)
    while True:
        seen = len(graph.get_state(config).values.get("messages", []))
        result = graph.invoke(graph_input, config)
        new = result["messages"][seen:]
        show_new_messages(new if isinstance(graph_input, Command) else new[1:], args.quiet)

        question = pending_question(result)
        if question:
            graph_input = Command(resume=ask_for_approval(question))
            continue

        text = input("You: ").strip()
        if text.lower() == "draft":
            print(json.dumps(to_configuration(result["draft"]), indent=2))
            text = input("You: ").strip()
        if text.lower() in ("quit", "exit"):
            approved = "approved" if result["draft"].get("approved") else "not approved"
            print(f"Final draft ({approved}, not saved):")
            print(json.dumps(to_configuration(result["draft"]), indent=2))
            return
        graph_input = client_message(graph, config, text, user_id=user_id,
                                     username=args.username)


if __name__ == "__main__":
    main()
