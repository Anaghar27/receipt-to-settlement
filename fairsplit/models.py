"""Data model for FairSplit. All money is stored as integer cents."""

import math
from dataclasses import dataclass, field, replace
from fractions import Fraction


@dataclass
class LineItem:
    """One purchasable line on a receipt."""

    name: str
    amount_cents: int  # the line total: what is actually split between people
    line_no: int  # 1-based line number in the original receipt text
    raw: str  # the original text of the line, for display and debugging
    quantity: int = 1
    unit_cents: int | None = None  # price of one unit, only when the receipt prints it ("2 @ 4.79")


@dataclass
class Receipt:
    """A parsed receipt. Summary fields are None when the receipt did not print them."""

    items: list[LineItem] = field(default_factory=list)
    subtotal_cents: int | None = None
    tax_cents: int = 0
    tip_cents: int = 0
    total_cents: int | None = None
    source: str = "rule"  # "rule" | "ocr" | "llm" | "mock"
    # The text of the line each summary field was read from, e.g. {"subtotal_cents": "SUB-TOTAL 39892.89"},
    # so validation can quote it when a value looks misread.
    summary_raw: dict[str, str] = field(default_factory=dict)


# Which people share each item: line_no -> list of person names.
Assignment = dict[int, list[str]]


@dataclass
class Result:
    """Final per-person amounts (in cents) and the settlement transfers."""

    pretax: dict[str, int]
    tax: dict[str, int]
    tip: dict[str, int]
    total: dict[str, int]
    settlements: list[tuple[str, str, int]]  # (from_person, to_person, amount_cents)


def percent_of(cents: int, percent: int) -> int:
    """percent% of an amount, rounded half up to a whole cent: 15% of 4585 -> 688."""
    return math.floor(Fraction(cents * percent, 100) + Fraction(1, 2))


def with_added_tip(receipt: Receipt, tip_cents: int) -> Receipt:
    """A copy of the receipt with a tip that was not printed on it.

    The tip is added on top of the printed total, so a receipt that reconciled
    still reconciles, and one that did not still does not.
    """
    if tip_cents < 0:
        raise ValueError("A tip cannot be negative.")
    total = None if receipt.total_cents is None else receipt.total_cents + tip_cents
    return replace(receipt, tip_cents=receipt.tip_cents + tip_cents, total_cents=total)


def format_cents(cents: int) -> str:
    """Format integer cents as dollars for display: 124102 -> "$1,241.02"."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"
