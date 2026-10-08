"""The rule types. Each takes a rule and the item it applies to, and returns
(passed, message): True, False, or None for "skipped" (a value it needs is
missing or unusable). Adding a type is a deliberate code change; which rules a
client uses is data.
"""

from datetime import date, timedelta

from rules.paths import is_multi, resolve

DEFAULT_TOLERANCE = 0.01


class Skip(Exception):
    """A value the rule needs is missing or unusable, so the rule doesn't apply."""


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number(item, path, optional=False):
    """The number a path points to. A path through a list gives the sum (0 if empty).

    Missing values raise Skip, unless optional, when they count as 0.
    """
    values = resolve(item, path)
    if is_multi(path):
        if any(v is None for v in values):
            raise Skip(f"{path} has a missing value")
        if not all(is_number(v) for v in values):
            raise Skip(f"{path} isn't all numbers")
        return sum(values)
    value = values[0]
    if value is None:
        if optional:
            return 0
        raise Skip(f"{path} is missing")
    if not is_number(value):
        raise Skip(f"{path} isn't a number")
    return value


def signed_term(term):
    """"+gross" -> (1, "gross", False); "-deductions[].amount" -> (-1, ...); "+vat?" optional."""
    sign = -1 if term.startswith("-") else 1
    path = term.lstrip("+-")
    optional = path.endswith("?")
    return sign, path.rstrip("?"), optional


def sum_equals(rule, item, _today):
    """The signed terms add up to the `equals` value, within the tolerance."""
    parts = []
    total = 0
    for term in rule["terms"]:
        sign, path, optional = signed_term(term)
        value = number(item, path, optional)
        total += sign * value
        parts.append(f"{'-' if sign < 0 else '+'} {value:.2f} ({path})")
    expected = number(item, rule["equals"])
    if abs(total - expected) <= rule.get("tolerance", DEFAULT_TOLERANCE):
        return True, None
    working = " ".join(parts).lstrip("+ ")
    return False, f"{working} = {total:.2f}, but {rule['equals']} is {expected:.2f}"


def percentage_of(rule, item, _today):
    """`value` is `percentage`% of `of`, within the tolerance."""
    value = number(item, rule["value"])
    percentage = number(item, rule["percentage"])
    of = number(item, rule["of"])
    expected = of * percentage / 100
    if abs(value - expected) <= rule.get("tolerance", DEFAULT_TOLERANCE):
        return True, None
    return False, (f"{rule['of']} {of:.2f} x {percentage:g}% = {expected:.2f}, "
                   f"but {rule['value']} is {value:.2f}")


COMPARISONS = {
    "=": lambda a, b, tol: abs(a - b) <= tol if is_number(a) else a == b,
    "!=": lambda a, b, tol: abs(a - b) > tol if is_number(a) else a != b,
    "<": lambda a, b, _tol: a < b,
    "<=": lambda a, b, _tol: a <= b,
    ">": lambda a, b, _tol: a > b,
    ">=": lambda a, b, _tol: a >= b,
}


def compare(rule, item, _today):
    """`left` `operator` `right`, for two numbers or two dates (YYYY-MM-DD text)."""
    left = resolve(item, rule["left"])[0]
    right = resolve(item, rule["right"])[0]
    for path, value in ((rule["left"], left), (rule["right"], right)):
        if value is None:
            raise Skip(f"{path} is missing")
    if is_number(left) != is_number(right) or not (
        is_number(left) or (isinstance(left, str) and isinstance(right, str))
    ):
        raise Skip(f"{rule['left']} and {rule['right']} can't be compared")
    operator = rule["operator"]
    if COMPARISONS[operator](left, right, rule.get("tolerance", DEFAULT_TOLERANCE)):
        return True, None
    return False, f"{rule['left']} ({left}) {operator} {rule['right']} ({right}) is false"


def date_within(rule, item, today):
    """Every date in `fields` is within `days_before`/`days_after` of today.

    Catches dates that are valid but implausible, like 0501-01-06 from a model
    that put the digits in the wrong order. Missing dates are ignored; dates
    that don't parse are left to the schema check.
    """
    earliest = today - timedelta(days=rule["days_before"])
    latest = today + timedelta(days=rule["days_after"])
    checked, outside = 0, []
    for path in rule["fields"]:
        for value in resolve(item, path):
            if not isinstance(value, str):
                continue
            try:
                when = date.fromisoformat(value)
            except ValueError:
                continue
            checked += 1
            if not earliest <= when <= latest:
                outside.append(f"{path} = {value}")
    if checked == 0:
        raise Skip("no dates to check")
    if outside:
        return False, f"outside {earliest} to {latest}: " + ", ".join(outside)
    return True, None


RULE_TYPES = {
    "sum_equals": sum_equals,
    "percentage_of": percentage_of,
    "compare": compare,
    "date_within": date_within,
}
