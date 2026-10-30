"""Reconciliation checks and suspicious-line detection.

A receipt may be settled only when validate() returns zero errors.
"""

from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import combinations

from fairsplit.models import Assignment, LineItem, Receipt, format_cents

SUSPICIOUS_MEDIAN_MULTIPLE = 5
SUSPICIOUS_ABSOLUTE_CENTS = 20_000  # $200
MAX_QUANTITY_LINES_TO_TRY = 16  # 2**16 = 65,536 readings at most, well under a second


@dataclass
class Issue:
    """One validation finding. A hint, if present, is shown underneath the message.

    Messages name items, never line numbers; line_no is kept for the UI to highlight the row.
    """

    severity: str  # "error" | "warning" | "info" (what was checked; never blocks settlement)
    message: str
    line_no: int | None = None
    hint: str | None = None


def items_sum(receipt: Receipt) -> int:
    """Sum of all item amounts, in cents."""
    return sum(item.amount_cents for item in receipt.items)


def subtotal_includes_tax(receipt: Receipt) -> bool:
    """True for receipts whose SUB-TOTAL already has tax in it (subtotal + tip = total).

    Decided from the printed summary lines only, never from the items, so the items
    must still add up exactly to subtotal - tax for the receipt to reconcile.
    """
    return (
        receipt.subtotal_cents is not None
        and receipt.total_cents is not None
        and receipt.tax_cents > 0
        and receipt.subtotal_cents + receipt.tip_cents == receipt.total_cents
    )


def items_target(receipt: Receipt) -> int | None:
    """What the items should add up to: the subtotal (minus tax, if the subtotal
    includes it), or total - tax - tip when there is no subtotal."""
    if receipt.subtotal_cents is not None:
        if subtotal_includes_tax(receipt):
            return receipt.subtotal_cents - receipt.tax_cents
        return receipt.subtotal_cents
    if receipt.total_cents is not None:
        return receipt.total_cents - receipt.tax_cents - receipt.tip_cents
    return None


def summary_adds_up(receipt: Receipt) -> bool:
    """True if the printed subtotal, tax, tip and total agree with each other."""
    if subtotal_includes_tax(receipt):
        return True
    return receipt.subtotal_cents + receipt.tax_cents + receipt.tip_cents == receipt.total_cents


def reconciles(receipt: Receipt, items_total: int) -> bool:
    """True if these items would make the whole receipt add up exactly."""
    if receipt.total_cents is None:
        return False
    if receipt.subtotal_cents is None:
        return items_total + receipt.tax_cents + receipt.tip_cents == receipt.total_cents
    return items_total == items_target(receipt) and summary_adds_up(receipt)


def median_cents(amounts: list[int]) -> Fraction:
    """Exact median of the amounts (a Fraction, so an even count never needs a float)."""
    ordered = sorted(amounts)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return Fraction(ordered[middle])
    return Fraction(ordered[middle - 1] + ordered[middle], 2)


def check_summary_not_above_total(receipt: Receipt) -> list[Issue]:
    """Error if the subtotal, tax or tip is larger than the total, which is impossible
    (none of them can be negative), so one of the two lines was misread. Quotes both."""
    if receipt.total_cents is None:
        return []
    issues = []
    for field_name, label in [("subtotal_cents", "subtotal"), ("tax_cents", "tax"), ("tip_cents", "tip")]:
        value = getattr(receipt, field_name)
        if value is None or value <= receipt.total_cents:
            continue
        quoted = [receipt.summary_raw.get(name) for name in (field_name, "total_cents")]
        says = " and ".join(f'"{line}"' for line in quoted if line)
        message = (
            f"The {label} ({format_cents(value)}) is larger than the total "
            f"({format_cents(receipt.total_cents)}), which is impossible, so one of the two was misread."
        )
        hint = f"The receipt text says {says}. Check them against the receipt." if says else None
        issues.append(Issue("error", message, hint=hint))
    return issues


def check_has_items(receipt: Receipt) -> list[Issue]:
    """Error if the parser found no items at all."""
    if receipt.items:
        return []
    return [Issue("error", "No items were found on the receipt.")]


def check_items_vs_subtotal(receipt: Receipt) -> list[Issue]:
    """Error if items do not sum exactly to the printed subtotal."""
    if receipt.subtotal_cents is None:
        return [
            Issue(
                "warning",
                "No SUBTOTAL line was found, so items + tax + tip are checked against the total instead.",
            )
        ]
    issues = []
    if subtotal_includes_tax(receipt):
        issues.append(
            Issue(
                "info",
                f"This receipt's subtotal already includes tax: subtotal "
                f"{format_cents(receipt.subtotal_cents)} + tip {format_cents(receipt.tip_cents)} "
                f"= total {format_cents(receipt.total_cents)}. So the items should add up to "
                f"{format_cents(receipt.subtotal_cents)} minus {format_cents(receipt.tax_cents)} tax "
                f"= {format_cents(items_target(receipt))}.",
            )
        )
    total = items_sum(receipt)
    target = items_target(receipt)
    gap = target - total
    if gap == 0:
        return issues
    hint = None
    if gap > total:
        # Several items may be missing, or the subtotal itself may be misread: say both.
        hint = (
            f"Items worth {format_cents(gap)} may be missing or unreadable, or the subtotal "
            "may be misread: the gap is more than all the items found together."
        )
    elif gap > 0:
        hint = f"An item worth {format_cents(gap)} may be missing or unreadable."
    what = "the subtotal minus tax" if subtotal_includes_tax(receipt) else "the subtotal"
    message = (
        f"Items sum to {format_cents(total)} but {what} is "
        f"{format_cents(target)} (mismatch {format_cents(abs(gap))})."
    )
    return issues + [Issue("error", message, hint=hint)]


def check_total(receipt: Receipt) -> list[Issue]:
    """Error if subtotal + tax + tip does not equal the printed total."""
    if receipt.total_cents is None:
        return [Issue("error", "No TOTAL line was found, so the receipt cannot be verified.")]
    if subtotal_includes_tax(receipt):
        return []  # subtotal + tip = total, by definition of this layout
    subtotal = items_sum(receipt) if receipt.subtotal_cents is None else receipt.subtotal_cents
    expected = subtotal + receipt.tax_cents + receipt.tip_cents
    if expected == receipt.total_cents:
        return []
    label = "Items" if receipt.subtotal_cents is None else "Subtotal"
    message = (
        f"{label} + tax + tip = {format_cents(expected)} but the total is "
        f"{format_cents(receipt.total_cents)} (mismatch {format_cents(abs(expected - receipt.total_cents))})."
    )
    return [Issue("error", message)]


def check_assignment(receipt: Receipt, assignment: Assignment) -> list[Issue]:
    """Error for every item that nobody is assigned to."""
    return [
        Issue("error", f"{item.name} is not assigned to anyone.", item.line_no)
        for item in receipt.items
        if not assignment.get(item.line_no)
    ]


def is_suspicious(item: LineItem, median: Fraction) -> bool:
    """An item over 5x the median item price, or over $200."""
    return (
        item.amount_cents > SUSPICIOUS_MEDIAN_MULTIPLE * median
        or item.amount_cents > SUSPICIOUS_ABSOLUTE_CENTS
    )


def divide_by_100_hint(receipt: Receipt, item: LineItem) -> str | None:
    """Hint if reading this item's price as cents (e.g. 1187 -> $11.87) reconciles the receipt."""
    if item.amount_cents % 100 != 0:
        return None
    corrected = item.amount_cents // 100
    if not reconciles(receipt, items_sum(receipt) - item.amount_cents + corrected):
        return None
    return (
        f"{item.name} may be {format_cents(corrected)}, not "
        f"{format_cents(item.amount_cents)}. That would reconcile the receipt."
    )


def check_suspicious_lines(receipt: Receipt) -> list[Issue]:
    """Warning for each unusually expensive item, with a divide-by-100 hint when it fits."""
    if not receipt.items:
        return []
    median = median_cents([item.amount_cents for item in receipt.items])
    return [
        Issue(
            "warning",
            f"{item.name} looks suspicious at {format_cents(item.amount_cents)}.",
            item.line_no,
            divide_by_100_hint(receipt, item),
        )
        for item in receipt.items
        if is_suspicious(item, median)
    ]


def check_quantities(receipt: Receipt) -> list[Issue]:
    """Warning when a printed "quantity @ unit price" does not match the line total."""
    return [
        Issue(
            "warning",
            f"{item.name}: {item.quantity} × {format_cents(item.unit_cents)} = "
            f"{format_cents(item.quantity * item.unit_cents)}, but the line total is "
            f"{format_cents(item.amount_cents)}.",
            item.line_no,
        )
        for item in receipt.items
        if item.unit_cents is not None and item.quantity * item.unit_cents != item.amount_cents
    ]


def resolve_unit_prices(receipt: Receipt) -> tuple[Receipt, list[Issue]]:
    """Decide whether "TACOS  3  4.00" means $4.00 for the line or 3 × $4.00 each.

    The parser reads every amount as the line total. If the items then do not add up,
    try reading some quantity lines as "price each". Apply a reading only if it is the
    ONLY one that makes the items match the receipt exactly, and say so with a warning.
    Otherwise leave the receipt as written, and say what was checked with an "info"
    note; validation will then block settlement.
    """
    target = items_target(receipt)
    unclear = [i for i in receipt.items if i.quantity > 1 and i.unit_cents is None]
    if target is None or not unclear or items_sum(receipt) == target:
        return receipt, []
    if check_summary_not_above_total(receipt):
        return receipt, []  # the target itself is misread, so no reading can be trusted
    if receipt.subtotal_cents is None:
        what = "total minus tax and tip"
    else:
        what = "subtotal minus tax" if subtotal_includes_tax(receipt) else "subtotal"
    checked = f"Also checked reading the {len(unclear)} quantity line(s) as \"price each\": "
    if len(unclear) > MAX_QUANTITY_LINES_TO_TRY:
        message = (
            f"Did not check reading the {len(unclear)} quantity lines as \"price each\": "
            f"more than {MAX_QUANTITY_LINES_TO_TRY} is too many combinations. "
            "The amounts are kept as line totals."
        )
        return receipt, [Issue("info", message)]
    readings = [
        lines
        for size in range(1, len(unclear) + 1)
        for lines in combinations(unclear, size)
        if items_sum(receipt) + sum((i.quantity - 1) * i.amount_cents for i in lines) == target
    ]
    if not readings:
        message = (
            f"{checked}no reading matches the {what} of {format_cents(target)}, "
            "so the amounts are kept as line totals."
        )
        return receipt, [Issue("info", message)]
    if len(readings) > 1:
        message = (
            f"{checked}{len(readings)} different readings match the {what}, so FairSplit "
            "will not guess. Correct the line totals in the table."
        )
        return receipt, [Issue("info", message)]
    each_price = {item.line_no for item in readings[0]}
    items, notes = [], []
    for item in receipt.items:
        if item.line_no in each_price:
            line_total = item.quantity * item.amount_cents
            notes.append(
                Issue(
                    "warning",
                    f"{item.name}: read as {item.quantity} × {format_cents(item.amount_cents)} = "
                    f"{format_cents(line_total)}, the only reading that matches the receipt. "
                    "Change the price in the table if that is wrong.",
                    item.line_no,
                )
            )
            item = replace(item, amount_cents=line_total, unit_cents=item.amount_cents)
        items.append(item)
    return replace(receipt, items=items), notes


def validate(receipt: Receipt, assignment: Assignment | None = None) -> list[Issue]:
    """Run every check. Pass assignment=None to check only the receipt itself."""
    issues = check_has_items(receipt)
    impossible = check_summary_not_above_total(receipt)
    if impossible:
        issues += impossible  # the sum checks would only repeat this misread in a confusing way
    else:
        issues += check_items_vs_subtotal(receipt)
        issues += check_total(receipt)
    if assignment is not None:
        issues += check_assignment(receipt, assignment)
    issues += check_quantities(receipt)
    issues += check_suspicious_lines(receipt)
    return issues


def can_settle(issues: list[Issue]) -> bool:
    """Settlement is allowed only when there are zero errors."""
    return not any(issue.severity == "error" for issue in issues)
