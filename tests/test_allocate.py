import random
from fractions import Fraction
from pathlib import Path

import pytest

from fairsplit.allocate import STRATEGIES, allocate, round_to_total
from fairsplit.models import LineItem, Receipt, percent_of, with_added_tip
from fairsplit.parser import parse_receipt
from fairsplit.validate import can_settle, validate

SAMPLES = Path(__file__).parent.parent / "samples"
PEOPLE = ["Anagha", "Priya", "Meera"]
ALL = PEOPLE


def load(name: str) -> Receipt:
    return parse_receipt((SAMPLES / name).read_text())


def by_name(receipt: Receipt, assignment_by_name: dict[str, list[str]]) -> dict[int, list[str]]:
    """Build an Assignment keyed by line_no from item names, for readable tests."""
    return {item.line_no: assignment_by_name[item.name] for item in receipt.items}


GROCERY_ASSIGNMENT = {
    "MILK 2% GAL": ALL, "EGGS DOZEN": ALL, "BASMATI RICE 10LB": ALL, "OLIVE OIL": ALL,
    "GREEK YOGURT": ["Anagha"], "SPINACH": ["Anagha"],
    "CHICKEN BREAST": ["Priya"], "OAT MILK": ["Priya"],
    "PANEER": ["Meera"],
}

DINNER_ASSIGNMENT = {
    "MARGHERITA PIZZA": ["Anagha", "Priya"], "CAESAR SALAD": ["Anagha"],
    "PAD THAI": ["Priya"], "GARLIC NAAN": ALL,
    "MANGO LASSI": ["Meera"], "TIRAMISU": ["Priya", "Meera"],
}


def make_receipt(amounts: list[int], tax: int = 0, tip: int = 0) -> Receipt:
    items = [LineItem(f"ITEM {n}", cents, n, "") for n, cents in enumerate(amounts, 1)]
    subtotal = sum(amounts)
    return Receipt(items, subtotal, tax, tip, subtotal + tax + tip)


def assert_adds_up(receipt: Receipt, shares: dict[str, dict[str, int]]) -> None:
    """Every column sums to the receipt, and every row's parts sum to that person's total."""
    assert sum(shares["total"].values()) == receipt.total_cents
    assert sum(shares["tax"].values()) == receipt.tax_cents
    assert sum(shares["tip"].values()) == receipt.tip_cents
    assert sum(shares["pretax"].values()) == receipt.subtotal_cents
    for person in shares["total"]:
        parts = shares["pretax"][person] + shares["tax"][person] + shares["tip"][person]
        assert parts == shares["total"][person]


# --- Acceptance examples ---

def test_grocery_example_gives_exact_expected_cents():
    receipt = load("synthetic_grocery.txt")
    shares = allocate(receipt, by_name(receipt, GROCERY_ASSIGNMENT), PEOPLE)
    assert shares["total"] == {"Anagha": 2057, "Priya": 2870, "Meera": 1872}
    assert shares["tax"] == {"Anagha": 63, "Priya": 89, "Meera": 58}
    assert shares["pretax"] == {"Anagha": 1994, "Priya": 2781, "Meera": 1814}
    assert_adds_up(receipt, shares)


def test_dinner_tip_is_allocated_proportionally_and_sums_to_total():
    receipt = load("synthetic_dinner_with_tip.txt")
    shares = allocate(receipt, by_name(receipt, DINNER_ASSIGNMENT), PEOPLE)
    # Exact totals 2664.46 / 3676.31 / 1424.23: the one leftover cent goes to Anagha.
    assert shares["total"] == {"Anagha": 2665, "Priya": 3676, "Meera": 1424}
    # Exact tips 378.14 / 521.73 / 202.13: the one leftover cent goes to Priya.
    assert shares["tip"] == {"Anagha": 378, "Priya": 522, "Meera": 202}
    assert_adds_up(receipt, shares)


# --- Rounding ---

def test_round_to_total_gives_leftover_cents_to_largest_remainders():
    exact = {"A": Fraction(205686, 100), "B": Fraction(286998, 100), "C": Fraction(187216, 100)}
    assert round_to_total(exact, 6799) == {"A": 2057, "B": 2870, "C": 1872}


def test_round_to_total_breaks_ties_by_entry_order():
    third = Fraction(1, 3)
    assert round_to_total({"A": third, "B": third, "C": third}, 1) == {"A": 1, "B": 0, "C": 0}
    assert round_to_total({"C": third, "B": third, "A": third}, 1) == {"C": 1, "B": 0, "A": 0}


def test_round_to_total_refuses_shares_that_do_not_add_up():
    with pytest.raises(ValueError):
        round_to_total({"A": Fraction(1), "B": Fraction(1)}, 3)


def random_receipt(rng: random.Random, realistic: bool) -> tuple[Receipt, dict[int, list[str]], list[str]]:
    people = ["Anagha", "Priya", "Meera", "Ravi", "Lena"][: rng.randint(2, 5)]
    amounts = [rng.randint(1, 20_000) for _ in range(rng.randint(1, 12))]
    if realistic:  # tax 0-15%, tip 0-30% of the subtotal
        tax = sum(amounts) * rng.randint(0, 15) // 100
        tip = sum(amounts) * rng.randint(0, 30) // 100
    else:  # anything goes, including tax and tip larger than the items
        tax, tip = rng.randint(0, 5_000), rng.randint(0, 5_000)
    receipt = make_receipt(amounts, tax, tip)
    assignment = {
        item.line_no: rng.sample(people, rng.randint(1, len(people))) for item in receipt.items
    }
    return receipt, assignment, people


def test_property_shares_always_sum_exactly_and_totals_are_never_negative():
    rng = random.Random(7)  # fixed seed: random but repeatable
    for _ in range(3_000):
        receipt, assignment, people = random_receipt(rng, realistic=False)
        shares = allocate(receipt, assignment, people, rng.choice(list(STRATEGIES)))
        assert_adds_up(receipt, shares)
        assert min(shares["total"].values()) >= 0
        assert min(shares["tax"].values()) >= 0
        assert min(shares["tip"].values()) >= 0


def test_property_pretax_is_never_negative_on_realistic_receipts():
    # Pre-tax is total - tax - tip, so it can show -$0.01 only when tax + tip are as
    # large as a person's items. On receipts with realistic rates it never does.
    rng = random.Random(8)
    for _ in range(3_000):
        receipt, assignment, people = random_receipt(rng, realistic=True)
        shares = allocate(receipt, assignment, people, rng.choice(list(STRATEGIES)))
        assert_adds_up(receipt, shares)
        assert min(shares["pretax"].values()) >= 0


def test_property_each_total_is_within_one_cent_of_exact():
    rng = random.Random(9)
    for _ in range(1_000):
        receipt, assignment, people = random_receipt(rng, realistic=True)
        shares = allocate(receipt, assignment, people)
        for person in people:
            mine = [i for i in receipt.items if person in assignment[i.line_no]]
            pretax = sum(Fraction(i.amount_cents, len(assignment[i.line_no])) for i in mine)
            exact = pretax * receipt.total_cents / receipt.subtotal_cents  # proportional
            assert abs(shares["total"][person] - exact) < 1


# --- Edge cases ---

def test_one_cent_split_three_ways():
    receipt = make_receipt([1])
    shares = allocate(receipt, {1: PEOPLE}, PEOPLE)
    assert shares["total"] == {"Anagha": 1, "Priya": 0, "Meera": 0}


def test_one_item_shared_by_everyone():
    receipt = make_receipt([900], tax=90)
    shares = allocate(receipt, {1: PEOPLE}, PEOPLE)
    assert shares["total"] == {"Anagha": 330, "Priya": 330, "Meera": 330}


def test_person_assigned_nothing_owes_zero_under_both_strategies():
    receipt = make_receipt([1000, 500], tax=150, tip=200)
    for strategy in STRATEGIES:
        shares = allocate(receipt, {1: ["Anagha"], 2: ["Priya"]}, PEOPLE, strategy)
        assert shares["total"]["Meera"] == 0
        assert_adds_up(receipt, shares)


def test_zero_tax():
    receipt = make_receipt([1000, 500])
    shares = allocate(receipt, {1: ["Anagha"], 2: ["Priya", "Meera"]}, PEOPLE)
    assert shares["tax"] == {"Anagha": 0, "Priya": 0, "Meera": 0}
    assert shares["total"] == {"Anagha": 1000, "Priya": 250, "Meera": 250}


def test_unknown_person_in_assignment_is_rejected():
    with pytest.raises(ValueError, match="not in the people list"):
        allocate(make_receipt([100]), {1: ["Zoe"]}, PEOPLE)


def test_duplicate_people_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        allocate(make_receipt([100]), {1: ["Anagha"]}, ["Anagha", "Anagha"])


# --- Strategy swap ---

def test_equal_strategy_changes_only_tax_and_tip():
    receipt = load("synthetic_grocery.txt")
    assignment = by_name(receipt, GROCERY_ASSIGNMENT)
    proportional = allocate(receipt, assignment, PEOPLE, "proportional")
    equal = allocate(receipt, assignment, PEOPLE, "equal")

    assert equal["pretax"] == proportional["pretax"]
    assert equal["tax"] == {"Anagha": 70, "Priya": 70, "Meera": 70}
    assert equal["tax"] != proportional["tax"]
    assert equal["total"] == {"Anagha": 2064, "Priya": 2851, "Meera": 1884}
    assert_adds_up(receipt, equal)


def test_strategy_registry_has_both_rules():
    assert set(STRATEGIES) == {"proportional", "equal"}


# --- Tip added by the user (not printed on the receipt) ---

def test_percent_of_rounds_half_up_to_a_cent():
    assert percent_of(4585, 15) == 688  # 687.75
    assert percent_of(4585, 20) == 917  # 917.0
    assert percent_of(1050, 15) == 158  # 157.5 rounds up
    assert percent_of(0, 20) == 0


def test_added_tip_raises_tip_and_total_together():
    receipt = make_receipt([1000, 500], tax=120)
    tipped = with_added_tip(receipt, 300)
    assert (tipped.tip_cents, tipped.total_cents) == (300, 1920)
    assert (receipt.tip_cents, receipt.total_cents) == (0, 1620)  # original is unchanged
    assert validate(tipped) == []


def test_added_tip_does_not_hide_an_unreconciled_receipt():
    receipt = make_receipt([1000], tax=80)
    receipt.total_cents = 2000  # does not reconcile
    assert not can_settle(validate(with_added_tip(receipt, 200)))


def test_negative_added_tip_is_rejected():
    with pytest.raises(ValueError):
        with_added_tip(make_receipt([1000]), -1)


def test_added_tip_is_split_proportionally_and_sums_exactly():
    receipt = with_added_tip(make_receipt([3000, 1000, 585], tax=367), percent_of(4585, 20))
    shares = allocate(receipt, {1: ["Anagha"], 2: ["Priya"], 3: ["Meera"]}, PEOPLE)
    assert sum(shares["tip"].values()) == 917
    assert shares["tip"]["Anagha"] > shares["tip"]["Priya"] > shares["tip"]["Meera"]
    assert_adds_up(receipt, shares)
