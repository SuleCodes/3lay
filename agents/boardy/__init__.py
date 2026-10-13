"""Boardy: the onboarding agent that builds a client's configuration through a conversation.

An agent, unlike Orchy's fixed route: the model decides each turn whether to
reply to the client or call a tool, and every tool checks its piece of the
draft with the same code the pipeline uses. See "Boardy" in tools/Planner.md.
"""
