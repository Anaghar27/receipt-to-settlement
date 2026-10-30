from pathlib import Path

import pytest

from fairsplit.models import LineItem, Receipt
from fairsplit.parser import parse_receipt
from fairsplit.settle import UnreconciledReceiptError, describe, settle_receipt, settlements

SAMPLES = Path(__file__).parent.parent / "samples"
PEOPLE = ["Anagha", "Priya", "Meera"]
ALL = PEOPLE

GROCERY_ASSIGNMENT = {
    "MILK 2% GAL": ALL, "EGGS DOZEN": ALL, "BASMATI RICE 10LB": ALL, "OLIVE OIL": ALL,
    "GREEK YOGURT": ["Anagha"], "SPINACH": ["Anagha"],
    "CHICKEN BREAST": ["Priya"], "OAT MILK": ["Priya"],
    "PANEER": ["Meera"],
}


def load(name: str) -> Receipt:
    return parse_receipt((SAMPLES / name).read_text())


def grocery_assignment(receipt: Receipt) -> dict[int, list[str]]:
    return {item.line_no: GROCERY_ASSIGNMENT[item.name] for item in receipt.items}


def test_grocery_example_end_to_end():
    receipt = load("synthetic_grocery.txt")
    result = settle_receipt(receipt, grocery_assignment(receipt), PEOPLE, payer="Anagha")
    assert result.total == {"Anagha": 2057, "Priya": 2870, "Meera": 1872}
    assert [describe(s) for s in result.settlements] == [
        "Priya pays Anagha $28.70",
        "Meera pays Anagha $18.72",
    ]


def test_misread_receipt_is_never_settled():
    receipt = load("synthetic_grocery_misread.txt")
    with pytest.raises(UnreconciledReceiptError, match=r"Items sum to \$1,241.02"):
        settle_receipt(receipt, grocery_assignment(receipt), PEOPLE, payer="Anagha")


def test_missing_item_receipt_is_never_settled():
    receipt = load("synthetic_grocery_missing_item.txt")
    with pytest.raises(UnreconciledReceiptError, match=r"Items sum to \$58.90"):
        settle_receipt(receipt, grocery_assignment(receipt), PEOPLE, payer="Anagha")


def test_unassigned_item_blocks_settlement():
    receipt = load("synthetic_grocery.txt")
    assignment = grocery_assignment(receipt)
    assignment[receipt.items[0].line_no] = []
    with pytest.raises(UnreconciledReceiptError, match="not assigned"):
        settle_receipt(receipt, assignment, PEOPLE, payer="Anagha")


def test_person_with_zero_total_is_not_listed():
    assert settlements({"Anagha": 500, "Priya": 300, "Meera": 0}, payer="Anagha") == [
        ("Priya", "Anagha", 300)
    ]


def test_payer_with_the_only_items_has_no_settlements():
    item = LineItem("SOUP", 500, 1, "SOUP 5.00")
    receipt = Receipt([item], subtotal_cents=500, tax_cents=40, total_cents=540)
    result = settle_receipt(receipt, {1: ["Anagha"]}, PEOPLE, payer="Anagha")
    assert result.total == {"Anagha": 540, "Priya": 0, "Meera": 0}
    assert result.settlements == []


def test_payer_must_be_one_of_the_people():
    receipt = load("synthetic_grocery.txt")
    with pytest.raises(ValueError, match="Payer"):
        settle_receipt(receipt, grocery_assignment(receipt), PEOPLE, payer="Zoe")


def test_describe_formats_dollars():
    assert describe(("Priya", "Anagha", 2870)) == "Priya pays Anagha $28.70"
    assert describe(("Meera", "Anagha", 124102)) == "Meera pays Anagha $1,241.02"


def test_receipt_whose_subtotal_includes_tax_settles_exactly():
    receipt = parse_receipt(
        "SYNTHETIC TRATTORIA\n2 PASTA  30.00\n1 WINE  70.00\nTAX  8.88\n"
        "SUB-TOTAL  108.88\n20% GRAT  20.00\nTOTAL  128.88"
    )
    pasta, wine = (item.line_no for item in receipt.items)
    result = settle_receipt(receipt, {pasta: ["Priya", "Meera"], wine: ["Anagha"]}, PEOPLE, "Anagha")
    assert sum(result.total.values()) == 12888
    assert sum(result.tax.values()) == 888 and sum(result.tip.values()) == 2000
    assert sum(result.pretax.values()) == 10000  # the items, not the printed SUB-TOTAL
    assert result.total == {"Anagha": 9022, "Priya": 1933, "Meera": 1933}
