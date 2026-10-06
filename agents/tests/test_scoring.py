"""Unit tests for the scorer: hand-written documents, no files, no model calls."""

from evaluation.scoring import flatten, score

STATEMENT = {
    "issuer": "Example Talent Ltd",
    "total_net": 5740.0,
    "tax_code": None,
    "lines": [
        {
            "gross": 7000.0,
            "deductions": [
                {"category": "agency_commission", "amount": 1050.0},
                {"category": "vat_on_commission", "amount": 210.0},
            ],
        }
    ],
}


def copy_with(**changes):
    doc = {**STATEMENT, **changes}
    return doc


def test_flatten_gives_one_entry_per_leaf_with_its_path():
    flat = flatten(STATEMENT)
    assert flat["lines[0].deductions[1].amount"] == 210.0
    assert flat["tax_code"] is None
    assert len(flat) == 8


def test_empty_list_is_a_field_in_its_own_right():
    assert flatten({"deductions": []}) == {"deductions": []}


def test_identical_documents_score_100():
    result = score(STATEMENT, STATEMENT)
    assert result["percent"] == 100.0
    assert result["mismatches"] == []


def test_wrong_value_is_reported_with_its_path():
    result = score(STATEMENT, copy_with(total_net=5000.0))
    assert result["matched"] == result["total"] - 1
    assert result["mismatches"] == [("total_net", 5740.0, 5000.0)]


def test_money_within_a_penny_matches():
    assert score(STATEMENT, copy_with(total_net=5740.004))["percent"] == 100.0
    assert score(STATEMENT, copy_with(total_net=5740.02))["percent"] < 100.0


def test_text_ignores_case_and_extra_spaces():
    assert score(STATEMENT, copy_with(issuer="example  TALENT ltd"))["percent"] == 100.0


def test_missing_field_counts_as_wrong():
    output = {k: v for k, v in STATEMENT.items() if k != "issuer"}
    result = score(STATEMENT, output)
    assert ("issuer", "Example Talent Ltd", "<missing>") in result["mismatches"]


def test_invented_line_counts_as_wrong():
    extra = {"gross": 1.0, "deductions": []}
    result = score(STATEMENT, copy_with(lines=[*STATEMENT["lines"], extra]))
    assert result["total"] == 10
    assert result["matched"] == 8


def test_null_only_matches_null():
    assert score(STATEMENT, copy_with(tax_code="1257L"))["percent"] < 100.0


def test_true_does_not_equal_one():
    assert score({"flag": True}, {"flag": 1})["percent"] == 0.0
