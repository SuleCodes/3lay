"""Paths into an extraction, e.g. "gross", "lines[].net", "deductions[category=x].amount".

Our own small format rather than JSONPath, so it's easy to read and for Boardy
to write correctly:

    name              a field
    name[]            every item of a list
    name[key=value]   only list items whose key equals value
    a.b.c             fields inside fields

A path containing [] or a filter can match many values ("multi"); otherwise it
names exactly one value, which may be missing.
"""

import re

SEGMENT = r"[A-Za-z_][A-Za-z0-9_]*(?:\[\]|\[[A-Za-z_][A-Za-z0-9_]*=[^\]]+\])?"
PATH = rf"{SEGMENT}(?:\.{SEGMENT})*"
PATH_PATTERN = re.compile(rf"^{PATH}$")

# Groups: field name; what's inside the brackets (if any); a filter's key and value.
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_SEGMENT_PARTS = re.compile(rf"^({_NAME})(?:\[(\]|({_NAME})=([^\]]+)\]))?$")


def parse(path):
    """Splits a path into (field, list_mode, filter) segments; raises ValueError if malformed."""
    if not PATH_PATTERN.match(path):
        raise ValueError(f"Invalid path: {path!r}")
    segments = []
    for raw in path.split("."):
        name, bracket, key, value = _SEGMENT_PARTS.match(raw).groups()
        if bracket is None:
            segments.append((name, False, None))
        elif key is None:
            segments.append((name, True, None))
        else:
            segments.append((name, True, (key, value)))
    return segments


def is_multi(path):
    return any(list_mode for _, list_mode, _ in parse(path))


def resolve(document, path):
    """Every value the path points to, in order.

    A missing field gives None, so a single path always gives exactly one value.
    A missing or empty list gives no values. Filters compare as text, so
    [category=agency_commission] and [rate=15] both work.
    """
    current = [document]
    for name, list_mode, filter_ in parse(path):
        found = []
        for item in current:
            value = item.get(name) if isinstance(item, dict) else None
            if not list_mode:
                found.append(value)
            elif isinstance(value, list):
                for element in value:
                    if filter_ is None or (
                        isinstance(element, dict) and str(element.get(filter_[0])) == filter_[1]
                    ):
                        found.append(element)
        current = found
    return current
