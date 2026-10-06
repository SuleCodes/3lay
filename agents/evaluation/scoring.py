"""Scores an extraction against a hand-checked gold standard, field by field."""

MONEY_TOLERANCE = 0.01


def flatten(value, path=""):
    """Turn nested JSON into {"lines[0].gross": 7000.0, ...}, one entry per leaf field."""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            out.update(flatten(child, f"{path}.{key}" if path else key))
        return out
    if isinstance(value, list) and value:
        out = {}
        for i, child in enumerate(value):
            out.update(flatten(child, f"{path}[{i}]"))
        return out
    return {path: value}  # a leaf: string, number, bool, None, or an empty list


def values_match(expected, actual):
    """Numbers within the money tolerance, text ignoring case and spacing, else exact."""
    # bool is a subclass of int (True == 1), so booleans only match booleans.
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    numbers = (int, float)
    if isinstance(expected, numbers) and isinstance(actual, numbers):
        return abs(expected - actual) <= MONEY_TOLERANCE
    if isinstance(expected, str) and isinstance(actual, str):
        return " ".join(expected.split()).casefold() == " ".join(actual.split()).casefold()
    return expected == actual


def score(gold, output):
    """Compare every field in either document; report the score and what differed.

    Fields missing from the output and fields the output invented both count
    as wrong, so a model can't score well by leaving things out or adding junk.
    """
    g, o = flatten(gold), flatten(output)
    paths = sorted(set(g) | set(o))
    mismatches = [
        (p, g.get(p, "<missing>"), o.get(p, "<missing>"))
        for p in paths
        if p not in g or p not in o or not values_match(g[p], o[p])
    ]
    matched = len(paths) - len(mismatches)
    return {
        "matched": matched,
        "total": len(paths),
        "percent": round(100 * matched / len(paths), 1) if paths else 100.0,
        "mismatches": mismatches,
    }
