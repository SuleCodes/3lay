"""Compares a run's checks with the gold standard's expected checks."""

CHECK_LABELS = {True: "pass", False: "fail", None: "skip"}


def compare_checks(expected, actual_checks):
    """(matched, total, differences): did each check come out as the gold standard says?

    expected is {check name: True/False/None}; actual_checks is the run's list
    of {"name", "passed", ...}. A check missing from either side is a difference.
    """
    actual = {c["name"]: c["passed"] for c in actual_checks}

    def label(checks, name):
        return CHECK_LABELS[checks[name]] if name in checks else "<missing>"

    names = list(expected) + [n for n in actual if n not in expected]
    differences = [
        (name, label(expected, name), label(actual, name))
        for name in names
        if name not in expected or name not in actual or expected[name] != actual[name]
    ]
    return len(names) - len(differences), len(names), differences
