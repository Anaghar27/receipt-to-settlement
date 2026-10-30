from pathlib import Path

from fairsplit.models import LineItem, Receipt, format_cents
from fairsplit.parser import parse_receipt
from fairsplit.validate import can_settle, median_cents, resolve_unit_prices, validate

SAMPLES = Path(__file__).parent.parent / "samples"


def load(name: str) -> Receipt:
    return parse_receipt((SAMPLES / name).read_text())


def everyone(receipt: Receipt) -> dict[int, list[str]]:
    """Assign every item to one person, so the assignment check passes."""
    return {item.line_no: ["Anagha"] for item in receipt.items}


def make_receipt(amounts: list[int], tax: int = 0, tip: int = 0) -> Receipt:
    """A synthetic receipt whose printed subtotal and total match its items."""
    items = [LineItem(f"ITEM {n}", cents, n, f"ITEM {n} {cents}") for n, cents in enumerate(amounts, 1)]
    subtotal = sum(amounts)
    return Receipt(items, subtotal, tax, tip, subtotal + tax + tip)


def errors(issues):
    return [i for i in issues if i.severity == "error"]


def warnings(issues):
    return [i for i in issues if i.severity == "warning"]


# --- The four samples (acceptance criteria) ---

def test_valid_grocery_receipt_has_no_issues():
    receipt = load("synthetic_grocery.txt")
    issues = validate(receipt, everyone(receipt))
    assert issues == []
    assert can_settle(issues)


def test_valid_dinner_receipt_with_tip_has_no_issues():
    receipt = load("synthetic_dinner_with_tip.txt")
    assert validate(receipt, everyone(receipt)) == []


def test_misread_receipt_reports_mismatch_and_flags_chicken_with_hint():
    receipt = load("synthetic_grocery_misread.txt")
    issues = validate(receipt, everyone(receipt))

    [error] = errors(issues)
    assert error.message == (
        "Items sum to $1,241.02 but the subtotal is $65.89 (mismatch $1,175.13)."
    )
    assert error.hint is None  # items are over the subtotal, not short of it

    [warning] = warnings(issues)
    assert warning.line_no == 6
    assert "CHICKEN BREAST" in warning.message
    assert warning.message == "CHICKEN BREAST looks suspicious at $1,187.00."
    assert warning.hint == (
        "CHICKEN BREAST may be $11.87, not $1,187.00. That would reconcile the receipt."
    )
    assert not can_settle(issues)


def test_missing_item_receipt_reports_mismatch_and_missing_amount_hint():
    receipt = load("synthetic_grocery_missing_item.txt")
    issues = validate(receipt, everyone(receipt))

    [error] = errors(issues)
    assert error.message == "Items sum to $58.90 but the subtotal is $65.89 (mismatch $6.99)."
    assert error.hint == "An item worth $6.99 may be missing or unreadable."
    assert warnings(issues) == []
    assert not can_settle(issues)


# --- Individual checks ---

def test_total_mismatch_is_an_error():
    receipt = make_receipt([500, 300], tax=80)
    receipt.total_cents = 900  # should be 880
    [error] = errors(validate(receipt))
    assert error.message == "Subtotal + tax + tip = $8.80 but the total is $9.00 (mismatch $0.20)."


def test_tip_counts_toward_the_total():
    receipt = make_receipt([1000], tax=100, tip=200)
    assert validate(receipt) == []
    receipt.tip_cents = 0  # forgetting the tip breaks reconciliation
    assert len(errors(validate(receipt))) == 1


def test_unassigned_item_is_an_error_naming_the_line():
    receipt = make_receipt([500, 300])
    issues = validate(receipt, {1: ["Anagha"], 2: []})
    [error] = errors(issues)
    assert error.line_no == 2
    assert error.message == "ITEM 2 is not assigned to anyone."
    # A line missing from the assignment entirely counts as unassigned too.
    assert len(errors(validate(receipt, {1: ["Anagha"]}))) == 1


def test_assignment_is_not_checked_when_none_is_given():
    assert validate(make_receipt([500, 300])) == []


def test_missing_subtotal_is_a_warning_and_items_are_checked_against_total():
    receipt = make_receipt([500, 300], tax=80)
    receipt.subtotal_cents = None
    issues = validate(receipt)
    assert errors(issues) == []
    assert "No SUBTOTAL" in warnings(issues)[0].message

    receipt.total_cents = 1000  # items + tax = 880, not 1000
    [error] = errors(validate(receipt))
    assert error.message.startswith("Items + tax + tip = $8.80")


def test_missing_total_is_an_error():
    receipt = make_receipt([500])
    receipt.total_cents = None
    [error] = errors(validate(receipt))
    assert "No TOTAL" in error.message


def test_receipt_with_no_items_is_an_error():
    receipt = Receipt(items=[], subtotal_cents=0, total_cents=0)
    assert not can_settle(validate(receipt))


# --- Suspicious lines ---

def test_item_over_five_times_median_is_suspicious():
    receipt = make_receipt([100, 100, 100, 501])  # median 100, 501 > 500
    [warning] = warnings(validate(receipt))
    assert warning.line_no == 4


def test_item_at_exactly_five_times_median_is_not_suspicious():
    assert warnings(validate(make_receipt([100, 100, 100, 500]))) == []


def test_item_over_200_dollars_is_suspicious_even_near_median():
    receipt = make_receipt([20_001, 19_000, 19_500])
    [warning] = warnings(validate(receipt))
    assert warning.line_no == 1
    assert warnings(validate(make_receipt([20_000, 19_000]))) == []


def test_no_divide_by_100_hint_when_it_would_not_reconcile():
    receipt = make_receipt([100, 100, 100, 50_000])  # valid receipt, just one expensive item
    [warning] = warnings(validate(receipt))
    assert warning.hint is None


def test_median_of_even_count_is_exact():
    assert median_cents([1, 2]) == median_cents([2, 1]) == 1.5
    assert median_cents([329, 399, 449]) == 399


def test_format_cents():
    assert format_cents(124102) == "$1,241.02"
    assert format_cents(1) == "$0.01"
    assert format_cents(0) == "$0.00"
    assert format_cents(-699) == "-$6.99"


# --- Quantities ---

def test_quantity_times_unit_price_mismatch_is_a_warning():
    receipt = parse_receipt("OAT MILK 2 @ 4.79   9.68\nSUBTOTAL 9.68\nTOTAL 9.68")
    [warning] = warnings(validate(receipt))
    assert warning.message == "OAT MILK: 2 × $4.79 = $9.58, but the line total is $9.68."


def test_matching_quantity_times_unit_price_is_fine():
    receipt = parse_receipt("OAT MILK 2 @ 4.79   9.58\nSUBTOTAL 9.58\nTOTAL 9.58")
    assert validate(receipt) == []


def test_unit_price_reading_is_applied_when_it_is_the_only_one_that_reconciles():
    receipt = parse_receipt("TACOS   3   4.00\nSODA  2.00\nSUBTOTAL 14.00\nTOTAL 14.00")
    resolved, notes = resolve_unit_prices(receipt)
    assert [(i.name, i.amount_cents, i.unit_cents) for i in resolved.items] == [
        ("TACOS", 1200, 400), ("SODA", 200, None),
    ]
    [note] = notes
    assert note.severity == "warning"
    assert note.message.startswith("TACOS: read as 3 × $4.00 = $12.00")
    assert validate(resolved) == []
    assert receipt.items[0].amount_cents == 400  # the parsed receipt is not changed


def test_line_total_reading_is_kept_when_the_receipt_already_reconciles():
    receipt = parse_receipt("CEVICHE   2   33.90\nSUBTOTAL 33.90\nTOTAL 33.90")
    resolved, notes = resolve_unit_prices(receipt)
    assert resolved.items[0].amount_cents == 3390
    assert notes == []


def test_nothing_is_guessed_when_two_readings_reconcile():
    # Either TACOS or BURRITOS could be "2 x $5.00": ambiguous, so leave it and block.
    receipt = parse_receipt("TACOS  2  5.00\nBURRITOS  2  5.00\nSUBTOTAL 15.00\nTOTAL 15.00")
    resolved, notes = resolve_unit_prices(receipt)
    assert resolved == receipt
    [note] = notes
    assert note.severity == "info"
    assert note.message == (
        'Also checked reading the 2 quantity line(s) as "price each": 2 different readings '
        "match the subtotal, so FairSplit will not guess. Correct the line totals in the table."
    )
    assert not can_settle(validate(resolved))


def test_nothing_is_guessed_when_no_reading_reconciles():
    receipt = parse_receipt("TACOS  3  4.00\nSUBTOTAL 99.00\nTOTAL 99.00")
    resolved, notes = resolve_unit_prices(receipt)
    assert resolved == receipt
    [note] = notes
    assert note.severity == "info"
    assert note.message == (
        'Also checked reading the 1 quantity line(s) as "price each": no reading matches '
        "the subtotal of $99.00, so the amounts are kept as line totals."
    )


def test_unit_price_reading_uses_total_minus_tax_without_a_subtotal():
    receipt = parse_receipt("TACOS  3  4.00\nTAX 1.00\nTOTAL 13.00")
    resolved, notes = resolve_unit_prices(receipt)
    assert resolved.items[0].amount_cents == 1200
    assert len(notes) == 1


def test_huge_gap_suggests_missing_items_or_a_misread_subtotal():
    receipt = make_receipt([3000, 1000])
    receipt.subtotal_cents = receipt.total_cents = 10000  # e.g. OCR lost most of the items
    [error] = errors(validate(receipt))
    assert error.hint == (
        "Items worth $60.00 may be missing or unreadable, or the subtotal may be misread: "
        "the gap is more than all the items found together."
    )


def test_subtotal_larger_than_total_is_one_clear_error_quoting_both_lines():
    # OCR read "SUB-TOTAL : $39892.89" as "SUB-TOTAL 2 939892.09".
    receipt = parse_receipt("PASTA  30.00\nSUB-TOTAL 2 939892.09\nTOTAL $47221.09")
    issues = validate(receipt)
    [error] = errors(issues)  # replaces the two confusing sum errors
    assert error.message == (
        "The subtotal ($939,892.09) is larger than the total ($47,221.09), which is impossible, "
        "so one of the two was misread."
    )
    assert error.hint == (
        'The receipt text says "SUB-TOTAL 2 939892.09" and "TOTAL $47221.09". '
        "Check them against the receipt."
    )
    assert not can_settle(issues)


def test_tax_or_tip_larger_than_total_is_an_error():
    receipt = parse_receipt("PASTA 30.00\nSUBTOTAL 30.00\nTAX 9251.88\nTOTAL 32.40")
    [error] = errors(validate(receipt))
    assert error.message.startswith("The tax ($9,251.88) is larger than the total ($32.40)")
    receipt = parse_receipt("PASTA 30.00\nSUBTOTAL 30.00\nGRATUITY 600.00\nTOTAL 36.00")
    [error] = errors(validate(receipt))
    assert error.message.startswith("The tip ($600.00) is larger than the total")


def test_quantity_readings_are_not_tried_against_an_impossible_subtotal():
    receipt = parse_receipt("TACOS  3  4.00\nSUBTOTAL 1200.00\nTOTAL 12.00")
    assert resolve_unit_prices(receipt) == (receipt, [])


def test_gap_equal_to_the_items_found_is_still_a_missing_item():
    receipt = make_receipt([500])
    receipt.subtotal_cents = receipt.total_cents = 1000  # one of two $5.00 items was not read
    error = errors(validate(receipt))[0]
    assert error.hint == "An item worth $5.00 may be missing or unreadable."


def test_too_many_quantity_lines_says_it_did_not_check():
    lines = "\n".join(f"DISH {chr(65 + n)}  2  1.00" for n in range(17))
    receipt = parse_receipt(lines + "\nSUBTOTAL 99.00\nTOTAL 99.00")
    resolved, [note] = resolve_unit_prices(receipt)
    assert resolved == receipt
    assert note.severity == "info"
    assert note.message.startswith('Did not check reading the 17 quantity lines as "price each"')


def test_sixteen_quantity_lines_are_still_checked():
    # 15 lines of "2 x $1.00?" can add at most $15.00 as price each, so only reading
    # DESSERT as 3 x $50.00 closes the $100.00 gap: exactly one reading matches.
    dishes = "\n".join(f"DISH {chr(65 + n)}  2  1.00" for n in range(15))
    receipt = parse_receipt(dishes + "\nDESSERT  3  50.00\nSUBTOTAL 165.00\nTOTAL 165.00")
    assert len([i for i in receipt.items if i.quantity > 1]) == 16
    resolved, [note] = resolve_unit_prices(receipt)
    assert resolved.items[-1].amount_cents == 15000
    assert note.severity == "warning" and note.message.startswith("DESSERT: read as 3 × $50.00")
    assert can_settle(validate(resolved))  # DESSERT is also flagged as unusually expensive


# --- Receipts whose subtotal already includes tax ---
# Synthetic, in the layout of some restaurant checks: items, TAX, SUB-TOTAL (with tax), GRAT, TOTAL.
POST_TAX_SUBTOTAL = """SYNTHETIC TRATTORIA
2 PASTA          30.00
1 WINE           70.00
TAX               8.88
SUB-TOTAL       108.88
20% GRAT         20.00
TOTAL           128.88"""


def test_subtotal_that_includes_tax_reconciles_with_an_info_note():
    receipt = parse_receipt(POST_TAX_SUBTOTAL)
    issues = validate(receipt)
    assert can_settle(issues)
    [note] = issues
    assert note.severity == "info"
    assert note.message == (
        "This receipt's subtotal already includes tax: subtotal $108.88 + tip $20.00 = total "
        "$128.88. So the items should add up to $108.88 minus $8.88 tax = $100.00."
    )


def test_subtotal_that_includes_tax_still_catches_a_wrong_item():
    receipt = parse_receipt(POST_TAX_SUBTOTAL.replace("70.00", "78.00"))
    [error] = errors(validate(receipt))
    assert error.message == (
        "Items sum to $108.00 but the subtotal minus tax is $100.00 (mismatch $8.00)."
    )


def test_subtotal_that_includes_tax_still_catches_a_missing_item():
    receipt = parse_receipt(POST_TAX_SUBTOTAL.replace("1 WINE           70.00\n", ""))
    [error] = errors(validate(receipt))
    assert error.hint == (
        "Items worth $70.00 may be missing or unreadable, or the subtotal may be misread: "
        "the gap is more than all the items found together."
    )


def test_subtotal_without_tax_is_the_normal_layout():
    # Tax of 0: subtotal + tip = total is the normal layout, so no note.
    receipt = parse_receipt("SOUP 10.00\nSUBTOTAL 10.00\nTIP 2.00\nTOTAL 12.00")
    assert validate(receipt) == []


def test_normal_layout_with_a_wrong_total_is_not_mistaken_for_tax_included():
    # subtotal + tax + tip should be 11.50; total 10.50 does not equal subtotal + tip (10.00) either.
    receipt = parse_receipt("SOUP 10.00\nSUBTOTAL 10.00\nTAX 0.80\nTIP 0.70\nTOTAL 10.50")
    assert [i.message for i in errors(validate(receipt))] == [
        "Subtotal + tax + tip = $11.50 but the total is $10.50 (mismatch $1.00)."
    ]


def test_divide_by_100_hint_works_when_the_subtotal_includes_tax():
    receipt = parse_receipt(POST_TAX_SUBTOTAL.replace("70.00", "7000"))
    [warning] = warnings(validate(receipt))
    assert warning.hint == "WINE may be $70.00, not $7,000.00. That would reconcile the receipt."
