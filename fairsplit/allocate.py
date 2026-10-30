"""Allocation strategies and largest-remainder rounding.

All intermediate amounts are exact Fractions of a cent. Rounding to whole cents
happens once, at the very end, and always sums exactly to the receipt total.
"""

import math
from fractions import Fraction
from typing import Callable

from fairsplit.models import Assignment, Receipt

Shares = dict[str, Fraction]  # person -> exact amount in cents


def pretax_shares(receipt: Receipt, assignment: Assignment, people: list[str]) -> Shares:
    """Each item is split equally among the people assigned to it."""
    shares = {person: Fraction(0) for person in people}
    for item in receipt.items:
        sharers = assignment.get(item.line_no, [])
        if not sharers:
            raise ValueError(f"{item.name} is not assigned to anyone.")
        for person in sharers:
            if person not in shares:
                raise ValueError(f"{person!r} is assigned an item but is not in the people list.")
            shares[person] += Fraction(item.amount_cents, len(sharers))
    return shares


def split_equal(pretax: Shares, amount_cents: int) -> Shares:
    """Split an amount evenly among everyone who has at least one item."""
    buyers = [person for person, share in pretax.items() if share > 0] or list(pretax)
    return {
        person: Fraction(amount_cents, len(buyers)) if person in buyers else Fraction(0)
        for person in pretax
    }


def split_proportional(pretax: Shares, amount_cents: int) -> Shares:
    """Split an amount in proportion to each person's pre-tax share."""
    pretax_total = sum(pretax.values())
    if pretax_total == 0:
        return split_equal(pretax, amount_cents)
    return {person: amount_cents * share / pretax_total for person, share in pretax.items()}


# How tax and tip are split. Add an entry here to add a new rule.
STRATEGIES: dict[str, Callable[[Shares, int], Shares]] = {
    "proportional": split_proportional,
    "equal": split_equal,
}


def round_to_total(exact: Shares, target_cents: int) -> dict[str, int]:
    """Largest-remainder rounding: floor everyone, then hand out the leftover cents
    one at a time to the largest fractional remainders. Ties go to whoever was
    entered first. The result always sums to exactly target_cents.
    """
    if sum(exact.values()) != target_cents:
        raise ValueError("Exact shares do not add up to the target, so they cannot be rounded.")
    rounded = {person: math.floor(value) for person, value in exact.items()}
    leftover = target_cents - sum(rounded.values())
    # sorted() is stable, so equal remainders keep the order people were entered.
    by_remainder = sorted(exact, key=lambda person: exact[person] - rounded[person], reverse=True)
    for person in by_remainder[:leftover]:
        rounded[person] += 1
    return rounded


def allocate(
    receipt: Receipt, assignment: Assignment, people: list[str], strategy: str = "proportional"
) -> dict[str, dict[str, int]]:
    """Return final per-person cents as {"pretax": ..., "tax": ..., "tip": ..., "total": ...}.

    Each person's total is their exact total rounded by largest remainder, so the
    totals sum to the receipt total. Tax and tip columns are rounded the same way
    (so they sum to the receipt's tax and tip), and pre-tax is what remains of each
    person's total, so every row and every column adds up exactly.
    """
    if len(set(people)) != len(people):
        raise ValueError("People names must be unique.")
    split = STRATEGIES[strategy]
    pretax = pretax_shares(receipt, assignment, people)
    tax = split(pretax, receipt.tax_cents)
    tip = split(pretax, receipt.tip_cents)
    exact_total = {person: pretax[person] + tax[person] + tip[person] for person in people}

    total_cents = round_to_total(exact_total, receipt.total_cents)
    tax_cents = round_to_total(tax, receipt.tax_cents)
    tip_cents = round_to_total(tip, receipt.tip_cents)
    pretax_cents = {p: total_cents[p] - tax_cents[p] - tip_cents[p] for p in people}
    return {"pretax": pretax_cents, "tax": tax_cents, "tip": tip_cents, "total": total_cents}
