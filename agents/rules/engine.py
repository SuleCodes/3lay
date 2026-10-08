"""Checks rule definitions, and runs rules against an extraction.

check_rule_definitions runs when a configuration is loaded (later: when Boardy's
output is approved), so a mistyped rule fails loudly then, never halfway
through an event. run_rules turns each rule into checks for validate.
"""

from datetime import date

import jsonschema

from rules.paths import PATH, resolve
from rules.types import RULE_TYPES, Skip

PATH_SCHEMA = {"type": "string", "pattern": rf"^{PATH}$"}
TERM = {"type": "string", "pattern": rf"^[+-]{PATH}\??$"}
TOLERANCE = {"type": "number", "minimum": 0}

COMMON = {
    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
    "type": {"enum": sorted(RULE_TYPES)},
    "scope": PATH_SCHEMA,
    "on_fail": {"enum": ["needs_review", "reject"]},
    "message": {"type": "string"},
    "description": {"type": "string"},
}

# What each rule type needs, on top of the common fields.
TYPE_FIELDS = {
    "sum_equals": (
        {"terms": {"type": "array", "items": TERM, "minItems": 1},
         "equals": PATH_SCHEMA, "tolerance": TOLERANCE},
        ["terms", "equals"],
    ),
    "percentage_of": (
        {"value": PATH_SCHEMA, "percentage": PATH_SCHEMA, "of": PATH_SCHEMA,
         "tolerance": TOLERANCE},
        ["value", "percentage", "of"],
    ),
    "compare": (
        {"left": PATH_SCHEMA, "right": PATH_SCHEMA,
         "operator": {"enum": ["=", "!=", "<", "<=", ">", ">="]}, "tolerance": TOLERANCE},
        ["left", "operator", "right"],
    ),
    "date_within": (
        {"fields": {"type": "array", "items": PATH_SCHEMA, "minItems": 1},
         "days_before": {"type": "integer", "minimum": 0},
         "days_after": {"type": "integer", "minimum": 0}},
        ["fields", "days_before", "days_after"],
    ),
}


def rule_schema(rule_type):
    fields, required = TYPE_FIELDS[rule_type]
    return {
        "type": "object",
        "properties": {**COMMON, **fields},
        "required": ["id", "type", "on_fail", *required],
        "additionalProperties": False,
    }


def check_rule_definitions(rules):
    """Raises ValueError listing every problem in a list of rules; returns them if fine."""
    if not isinstance(rules, list):
        raise ValueError("Rules must be a list.")
    problems, seen = [], set()
    for i, rule in enumerate(rules):
        label = f"rule {i} ({rule.get('id', 'no id') if isinstance(rule, dict) else '?'})"
        if not isinstance(rule, dict):
            problems.append(f"{label}: must be an object")
            continue
        if rule.get("type") not in RULE_TYPES:
            problems.append(f"{label}: unknown type {rule.get('type')!r}; "
                            f"known types: {', '.join(sorted(RULE_TYPES))}")
            continue
        validator = jsonschema.Draft202012Validator(rule_schema(rule["type"]))
        for error in validator.iter_errors(rule):
            where = ".".join(str(p) for p in error.absolute_path) or "rule"
            problems.append(f"{label}: {where}: {error.message}")
        if rule.get("id") in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(rule.get("id"))
    if problems:
        raise ValueError("Invalid rules:\n  " + "\n  ".join(problems))
    return rules


def run_rule(rule, item, name, today):
    try:
        passed, message = RULE_TYPES[rule["type"]](rule, item, today)
    except Skip as reason:
        return {"name": name, "passed": None, "message": f"Skipped: {reason}",
                "rule": rule["id"], "on_fail": rule["on_fail"]}
    if not passed:
        message = f"{rule['message']}: {message}" if rule.get("message") else message
    return {"name": name, "passed": passed, "message": message,
            "rule": rule["id"], "on_fail": rule["on_fail"]}


def run_rules(rules, extraction, today=None):
    """One check per rule, or per item in its scope (e.g. per line, named "id[0]").

    passed is True, False, or None when skipped (a value it needs is missing).
    Rules never raise: unusable data makes them skip, so they're safe to run on
    any extraction, including one that failed the schema check.
    """
    today = today or date.today()
    checks = []
    for rule in rules:
        if "scope" not in rule:
            checks.append(run_rule(rule, extraction, rule["id"], today))
            continue
        items = resolve(extraction, rule["scope"])
        if not items:
            checks.append({"name": rule["id"], "passed": None,
                           "message": f"Skipped: no items in {rule['scope']}",
                           "rule": rule["id"], "on_fail": rule["on_fail"]})
        for i, item in enumerate(items):
            checks.append(run_rule(rule, item, f"{rule['id']}[{i}]", today))
    return checks
