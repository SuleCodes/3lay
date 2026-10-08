"""Tests for the rule engine: made-up documents and rules, nothing client-specific."""

from datetime import date

import pytest

from rules import check_rule_definitions, run_rules
from rules.paths import parse, resolve

TODAY = date(2026, 10, 8)

ORDER = {
    "shipped_on": "2026-09-30",
    "total": 30.0,
    "discount": None,
    "items": [
        {"price": 10.0, "fees": [{"kind": "tax", "amount": 2.0}, {"kind": "handling", "amount": 1.0}],
         "net": 7.0, "tax_rate": 20},
        {"price": 25.0, "fees": [], "net": 23.0, "tax_rate": None},
    ],
}


def rule(**fields):
    return {"id": "test_rule", "on_fail": "needs_review", **fields}


def results(rules, document=ORDER):
    return {c["name"]: c for c in run_rules(rules, document, TODAY)}


# Paths

def test_resolve_single_field_lists_and_filters():
    assert resolve(ORDER, "total") == [30.0]
    assert resolve(ORDER, "missing") == [None]
    assert resolve(ORDER, "items[].price") == [10.0, 25.0]
    assert resolve(ORDER, "items[].fees[].amount") == [2.0, 1.0]
    assert resolve(ORDER["items"][0], "fees[kind=tax].amount") == [2.0]
    assert resolve(ORDER["items"][1], "fees[].amount") == []  # empty list: no values


def test_malformed_paths_are_rejected():
    for bad in ["", "items[", "a..b", "items[x]", "1abc"]:
        with pytest.raises(ValueError):
            parse(bad)


# sum_equals

LINE_ADDS_UP = rule(type="sum_equals", scope="items[]",
                    terms=["+price", "+discount?", "-fees[].amount"], equals="net")


def test_sum_equals_passes_per_item_with_an_empty_list_counting_as_zero():
    checks = results([LINE_ADDS_UP])
    assert checks["test_rule[0]"]["passed"] is True  # 10 - 2 - 1 = 7
    assert checks["test_rule[1]"]["passed"] is False  # 25 - 0 = 25, net is 23
    assert "but net is 23.00" in checks["test_rule[1]"]["message"]


def test_sum_equals_over_a_list_against_a_document_total():
    checks = results([rule(type="sum_equals", terms=["+items[].net"], equals="total")])
    assert checks["test_rule"]["passed"] is True  # 7 + 23 = 30


def test_missing_value_skips_the_rule_unless_marked_optional():
    checks = results([rule(type="sum_equals", terms=["+total", "-discount"], equals="total")])
    assert checks["test_rule"]["passed"] is None
    assert "discount is missing" in checks["test_rule"]["message"]

    checks = results([rule(type="sum_equals", terms=["+total", "-discount?"], equals="total")])
    assert checks["test_rule"]["passed"] is True  # discount? counts as 0


def test_rule_failure_message_starts_with_the_rules_own_message():
    checks = results([{**LINE_ADDS_UP, "message": "Item doesn't add up"}])
    assert checks["test_rule[1]"]["message"].startswith("Item doesn't add up:")


# percentage_of

def test_percentage_of_uses_a_filter_and_skips_when_the_rate_is_missing():
    checks = results([rule(type="percentage_of", scope="items[]",
                           value="fees[kind=tax].amount", percentage="tax_rate", of="price")])
    assert checks["test_rule[0]"]["passed"] is True  # 20% of 10 = 2
    assert checks["test_rule[1]"]["passed"] is None  # no tax_rate printed


# compare

def test_compare_numbers_and_dates():
    document = {"start": "2026-01-01", "end": "2025-12-31", "low": 1, "high": 2}
    checks = results([
        rule(id="numbers", type="compare", left="low", operator="<", right="high"),
        rule(id="dates", type="compare", left="start", operator="<=", right="end"),
        rule(id="mixed", type="compare", left="low", operator="<", right="start"),
    ], document)
    assert checks["numbers"]["passed"] is True
    assert checks["dates"]["passed"] is False
    assert checks["mixed"]["passed"] is None  # a number and a date can't be compared


# date_within

DATES_PLAUSIBLE = rule(type="date_within", fields=["shipped_on", "items[].shipped_on"],
                       days_before=1095, days_after=31)


def test_date_within_passes_plausible_dates():
    assert results([DATES_PLAUSIBLE])["test_rule"]["passed"] is True


def test_date_within_catches_digits_in_the_wrong_order():
    checks = results([DATES_PLAUSIBLE], {"shipped_on": "0501-01-06"})
    assert checks["test_rule"]["passed"] is False
    assert "shipped_on = 0501-01-06" in checks["test_rule"]["message"]


def test_date_within_skips_when_there_are_no_dates():
    assert results([DATES_PLAUSIBLE], {"shipped_on": None})["test_rule"]["passed"] is None


# Scope and robustness

def test_scope_with_no_items_is_one_skipped_check():
    checks = results([LINE_ADDS_UP], {"items": []})
    assert checks["test_rule"]["passed"] is None


def test_rules_never_raise_on_bad_data():
    garbage = {"items": "not a list", "total": "thirty", "shipped_on": 5}
    checks = run_rules([LINE_ADDS_UP, DATES_PLAUSIBLE,
                        rule(id="total", type="sum_equals", terms=["+total"], equals="total")],
                       garbage, TODAY)
    assert all(c["passed"] is None for c in checks)


def test_checks_carry_the_rule_id_and_severity():
    check = results([{**LINE_ADDS_UP, "on_fail": "reject"}])["test_rule[0]"]
    assert check["rule"] == "test_rule"
    assert check["on_fail"] == "reject"


# Checking rule definitions (what Boardy's output will be checked against)

def test_valid_rules_are_accepted():
    rules = [LINE_ADDS_UP, {**DATES_PLAUSIBLE, "id": "dates"}]
    assert check_rule_definitions(rules) is rules


@pytest.mark.parametrize("bad_rule, problem", [
    (rule(type="average_of", field="x"), "unknown type 'average_of'"),
    (rule(type="sum_equals", terms=["+price"]), "'equals' is a required property"),
    (rule(type="sum_equals", terms=["price"], equals="net"), "does not match"),  # no sign
    (rule(type="sum_equals", terms=["+price"], equals="net", on_fail="ignore"), "on_fail"),
    (rule(type="compare", left="a", operator="equals", right="b"), "operator"),
    (rule(type="sum_equals", terms=["+price"], equals="net", extra="x"), "extra"),
    ({"type": "sum_equals", "terms": ["+a"], "equals": "b", "on_fail": "reject"}, "'id'"),
])
def test_bad_rules_are_rejected_with_a_clear_reason(bad_rule, problem):
    with pytest.raises(ValueError, match="Invalid rules") as error:
        check_rule_definitions([bad_rule])
    assert problem in str(error.value)


def test_duplicate_rule_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate id"):
        check_rule_definitions([LINE_ADDS_UP, LINE_ADDS_UP])
