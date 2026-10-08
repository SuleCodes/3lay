"""Generic rule engine: runs a client's rules (data) against an extraction.

The engine knows rule *types* only; which rules apply is configuration.
See "Rule format" in tools/Planner.md.
"""

from rules.engine import check_rule_definitions, run_rules

__all__ = ["check_rule_definitions", "run_rules"]
