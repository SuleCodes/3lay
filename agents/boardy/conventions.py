"""3lay's schema conventions, checked in code so every client's schema follows them.

They're what building Rolepay's schema by hand taught (tools/Planner.md):

- every field has a description: it's the instruction Obed reads
- every object lists all its fields as required, with null for "not on the
  document", and allows no extra fields: Obed must say explicitly that a
  value is missing, and strict structured output needs this shape
- money is a number
- no personal identifiers or payment details are collected
- the schema has an $id of the form "<name>/<version>"

Dates: the planner's convention is "copied as printed", converted in code.
That conversion isn't built yet, and today's rules (date_within) need
YYYY-MM-DD, so for now date fields only have to be text.
"""

import re

import jsonschema

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*/[0-9]+$")

# Field names that hold money, so must be numbers. Matched against the name's
# last word, so total_net and year_to_date_tax count but tax_code doesn't.
MONEY_WORDS = {"amount", "gross", "net", "total", "tax", "fee", "fees", "pay", "price",
               "cost", "salary", "commission", "deduction", "deductions", "vat",
               "balance", "payout"}

# Field names Boardy must never collect: identifiers and payment details.
FORBIDDEN = re.compile(
    r"(bank|account_number|sort_code|iban|swift|bic\b|card_number|cvv|"
    r"national_insurance|ni_number|nino|passport|password|social_security|ssn)")


def last_word(name):
    """year_to_date_gross -> gross: what a field holds is usually its last word."""
    return re.split(r"[^a-z0-9]+", name.lower())[-1]


def allowed_types(field):
    kind = field.get("type")
    return set(kind) if isinstance(kind, list) else {kind} if kind else set()


def check_field(path, field, problems):
    name = path.rsplit(".", 1)[-1].replace("[]", "")
    if not str(field.get("description", "")).strip():
        problems.append(f"{path}: needs a description (it's the instruction Obed reads)")
    if FORBIDDEN.search(name.lower()):
        problems.append(f"{path}: 3lay doesn't collect personal identifiers or payment details")
    kinds = allowed_types(field) - {"null"}
    holds = last_word(name)
    if holds in MONEY_WORDS and kinds and not kinds <= {"number", "integer"} \
            and not kinds & {"array", "object"}:
        problems.append(f"{path}: looks like money, so it must be a number")
    if holds == "date" and kinds and kinds != {"string"}:
        problems.append(f"{path}: a date must be text")
    check_object(path, field, problems)


def check_object(path, schema, problems):
    """Checks an object's fields, and any list's items, recursively."""
    if "object" in allowed_types(schema) or "properties" in schema:
        properties = schema.get("properties") or {}
        if not properties:
            problems.append(f"{path or 'the schema'}: an object needs at least one field")
        missing = sorted(set(properties) - set(schema.get("required", [])))
        if missing:
            problems.append(f"{path or 'the schema'}: list every field in required "
                            f"(use null for 'not on the document'): {', '.join(missing)}")
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path or 'the schema'}: set additionalProperties to false")
        for name, field in properties.items():
            check_field(f"{path}.{name}" if path else name, field, problems)
    if "array" in allowed_types(schema):
        items = schema.get("items")
        if not isinstance(items, dict):
            problems.append(f"{path}: a list needs an items schema")
        else:
            check_object(f"{path}[]", items, problems)


def check_schema_conventions(schema):
    """Every way the schema breaks JSON Schema or 3lay's conventions. Empty if it's fine."""
    if not isinstance(schema, dict):
        return ["The schema must be a JSON object"]
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return [f"Not a valid JSON Schema: {exc.message}"]
    problems = []
    if not ID_PATTERN.match(str(schema.get("$id", ""))):
        problems.append(f'$id is {schema.get("$id")!r}; it must be "<name>/<version>": the name '
                        'in lower-case letters, digits, "-" and "_", then "/" and a whole '
                        'number, e.g. "acme-invoice/1"')
    if schema.get("type") != "object":
        problems.append('the schema\'s type must be "object" (one document)')
    check_object("", schema, problems)
    return problems


def schema_at(schema, path):
    """The part of the schema a rule path points to, or None if it doesn't exist.

    Rule paths: "gross", "lines[].net", "deductions[category=x].amount".
    A filter ([key=value]) is treated like [] here: it selects list items.
    """
    current = schema
    for segment in path.split("."):
        name, _, rest = segment.partition("[")
        properties = current.get("properties") if isinstance(current, dict) else None
        if not properties or name not in properties:
            return None
        current = properties[name]
        if rest:
            if "array" not in allowed_types(current) or not isinstance(current.get("items"), dict):
                return None
            current = current["items"]
    return current
