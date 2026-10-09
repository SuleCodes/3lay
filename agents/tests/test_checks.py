"""Comparing a run's checks with the gold standard's expected checks."""

from evaluation.checks import compare_checks


def checks(**passed):
    return [{"name": name, "passed": value} for name, value in passed.items()]


def test_all_as_expected():
    assert compare_checks({"a": True, "b": None}, checks(a=True, b=None)) == (2, 2, [])


def test_a_check_with_a_different_result_is_reported():
    _, _, differences = compare_checks({"a": True}, checks(a=False))
    assert differences == [("a", "pass", "fail")]


def test_skip_is_not_the_same_as_pass():
    assert compare_checks({"a": None}, checks(a=True))[0] == 0


def test_checks_missing_from_either_side_are_differences():
    matched, total, differences = compare_checks({"a": True, "b": True}, checks(a=True, c=False))
    assert (matched, total) == (1, 3)
    assert ("b", "pass", "<missing>") in differences
    assert ("c", "<missing>", "fail") in differences
