import random
from pathlib import Path

import pytest

from fairsplit.parser import parse_receipt, price_to_cents

SAMPLES = Path(__file__).parent.parent / "samples"


def load(name: str):
    return parse_receipt((SAMPLES / name).read_text())


def test_grocery_sample_parses_nine_items_with_exact_cents():
    receipt = load("synthetic_grocery.txt")
    assert [(i.name, i.amount_cents) for i in receipt.items] == [
        ("MILK 2% GAL", 449),
        ("EGGS DOZEN", 399),
        ("BASMATI RICE 10LB", 1499),
        ("GREEK YOGURT", 549),
        ("CHICKEN BREAST", 1187),
        ("SPINACH", 329),
        ("OLIVE OIL", 999),
        ("OAT MILK", 479),
        ("PANEER", 699),
    ]
    assert receipt.subtotal_cents == 6589
    assert receipt.tax_cents == 210
    assert receipt.tip_cents == 0
    assert receipt.total_cents == 6799
    assert receipt.source == "rule"


def test_line_numbers_and_raw_text_point_at_the_original_line():
    receipt = load("synthetic_grocery.txt")
    chicken = receipt.items[4]
    assert chicken.line_no == 6  # line 1 is the header
    assert chicken.raw.startswith("CHICKEN BREAST")


def test_price_without_decimal_is_read_as_dollars_not_fixed():
    receipt = load("synthetic_grocery_misread.txt")
    chicken = receipt.items[4]
    assert chicken.name == "CHICKEN BREAST"
    assert chicken.amount_cents == 118700


def test_missing_item_sample_has_eight_items():
    receipt = load("synthetic_grocery_missing_item.txt")
    assert len(receipt.items) == 8
    assert "PANEER" not in [i.name for i in receipt.items]
    assert receipt.subtotal_cents == 6589


def test_dinner_sample_parses_tip_and_labels_with_percentages():
    receipt = load("synthetic_dinner_with_tip.txt")
    assert len(receipt.items) == 6
    assert receipt.subtotal_cents == 6120
    assert receipt.tax_cents == 543
    assert receipt.tip_cents == 1102
    assert receipt.total_cents == 7765
    assert sum(i.amount_cents for i in receipt.items) == receipt.subtotal_cents


def test_summary_labels_are_case_insensitive_and_gratuity_is_tip():
    receipt = parse_receipt("Soup 5.00\nsubtotal 5.00\nTax: 0.40\nGratuity 1.00\ntotal $6.40")
    assert [i.name for i in receipt.items] == ["Soup"]
    assert (receipt.subtotal_cents, receipt.tax_cents, receipt.tip_cents, receipt.total_cents) == (
        500, 40, 100, 640,
    )


@pytest.mark.parametrize(
    "line, field",
    [
        ("Total Taxes  $3.67", "tax_cents"),
        ("SALES TAX  3.67", "tax_cents"),
        ("Grand Total  $3.67", "total_cents"),
        ("Amount Due:  $3.67", "total_cents"),
        ("BALANCE DUE  3.67", "total_cents"),
        ("Sub Total  3.67", "subtotal_cents"),
        ("Sub-total  3.67", "subtotal_cents"),
        ("TIP 18%  3.67", "tip_cents"),
    ],
)
def test_summary_labels_match_on_any_word(line, field):
    receipt = parse_receipt(line)
    assert receipt.items == []
    assert getattr(receipt, field) == 367


def test_words_that_merely_contain_a_label_are_items():
    receipt = parse_receipt("TIPSY CAKE  7.00\nTAXI FARE  9.00\nSUBTOTAL  16.00")
    assert [i.name for i in receipt.items] == ["TIPSY CAKE", "TAXI FARE"]


def test_restaurant_style_summary_block():
    # Synthetic, in the style of a real guest check: tax says "Total", total is printed twice.
    receipt = parse_receipt(
        "SYNTHETIC CAFE\nSOUP  $8.95\nFISH  $15.95\nSubtotal  $24.90\nTotal Taxes  $1.99\n"
        "Grand Total  $26.89\nAmount Due:  $26.89\nTIP IS NOT INCLUDED"
    )
    assert [i.name for i in receipt.items] == ["SOUP", "FISH"]
    assert (receipt.subtotal_cents, receipt.tax_cents, receipt.tip_cents, receipt.total_cents) == (
        2490, 199, 0, 2689,
    )


def test_summary_lines_are_kept_as_written_for_quoting():
    receipt = parse_receipt("SOUP 10.00\nSUB-TOTAL : $10.00\nSTATE TAX 0.60\nCITY TAX 0.25\nTOTAL 10.85")
    assert receipt.summary_raw == {
        "subtotal_cents": "SUB-TOTAL : $10.00",
        "tax_cents": "STATE TAX 0.60",  # the first of several tax lines
        "total_cents": "TOTAL 10.85",
    }


def test_several_tax_lines_are_added_up():
    receipt = parse_receipt("SOUP 10.00\nSUBTOTAL 10.00\nSTATE TAX 0.60\nCITY TAX 0.25\nTOTAL 10.85")
    assert receipt.tax_cents == 85


def test_first_printed_total_is_kept():
    receipt = parse_receipt("SOUP 10.00\nTOTAL 10.00\nAMOUNT DUE 0.00")
    assert receipt.total_cents == 1000
    assert len(receipt.items) == 1


def test_quantity_column_is_not_part_of_the_name():
    receipt = parse_receipt("LIMONADA              1   $4.00\nCEVICHE DE CAMARONES  2  $33.90")
    assert [(i.name, i.amount_cents) for i in receipt.items] == [
        ("LIMONADA", 400), ("CEVICHE DE CAMARONES", 3390),
    ]
    assert receipt.items[0].raw == "LIMONADA              1   $4.00"  # original line kept


def test_numbers_inside_a_name_are_kept():
    receipt = parse_receipt("BASMATI RICE 10LB  14.99\nMILK 2% GAL  4.49\nCHIPS #2  1.50")
    assert [i.name for i in receipt.items] == ["BASMATI RICE 10LB", "MILK 2% GAL", "CHIPS #2"]


def test_line_with_no_letters_is_not_an_item():
    # A row of suggested tips under the total, as on many restaurant receipts.
    receipt = parse_receipt("SOUP  5.00\n  $7.43    $9.90   $12.38\n--- 4.00")
    assert [i.name for i in receipt.items] == ["SOUP"]


# --- Quantities (formats A-D) ---

def quantities(receipt):
    return [(i.name, i.quantity, i.unit_cents, i.amount_cents) for i in receipt.items]


def test_format_a_quantity_column_with_line_total():
    receipt = parse_receipt("CEVICHE DE CAMARONES   2   $33.90")
    assert quantities(receipt) == [("CEVICHE DE CAMARONES", 2, None, 3390)]


@pytest.mark.parametrize(
    "line",
    ["OAT MILK 2 @ 4.79   9.58", "OAT MILK  2 x $4.79  $9.58", "OAT MILK 2X4.79 9.58",
     "OAT MILK 2 @ 4.79 ea  9.58", "OAT MILK  2 × 4.79 each  9.58"],
)
def test_format_b_quantity_at_unit_price_then_line_total(line):
    assert quantities(parse_receipt(line)) == [("OAT MILK", 2, 479, 958)]


def test_format_c_quantity_line_under_the_item_name():
    receipt = parse_receipt("BANANAS\n  3 @ 0.25        0.75\nBREAD  2.50")
    assert quantities(receipt) == [("BANANAS", 3, 25, 75), ("BREAD", 1, None, 250)]
    assert receipt.items[0].line_no == 2  # the line with the price
    assert receipt.items[0].raw == "BANANAS\n  3 @ 0.25        0.75"


def test_format_c_needs_a_name_on_the_line_just_above():
    # A blank line in between: there is no name to attach, so it is not an item.
    receipt = parse_receipt("BANANAS\n\n  3 @ 0.25        0.75")
    assert receipt.items == []


def test_format_d_is_read_as_written_by_the_parser():
    # "TACOS 3 4.00" could be 3 x $4.00; the parser reads $4.00 as the line total and
    # records the quantity, and validate.resolve_unit_prices decides using the subtotal.
    assert quantities(parse_receipt("TACOS   3   4.00")) == [("TACOS", 3, None, 400)]


def test_quantity_at_the_start_of_the_line():
    receipt = parse_receipt("2 ESPRESSO   $15.00\n5 TAWNY 40   275,00\n1 TIRAMISU   15.00")
    assert quantities(receipt) == [
        ("ESPRESSO", 2, None, 1500), ("TAWNY 40", 5, None, 27500), ("TIRAMISU", 1, None, 1500),
    ]


def test_leading_numbers_that_are_not_quantities_stay_in_the_name():
    receipt = parse_receipt("2% MILK  4.49\n10LB RICE  14.99\n2D TRUFFLE  200.00")
    assert [i.name for i in receipt.items] == ["2% MILK", "10LB RICE", "2D TRUFFLE"]
    assert [i.quantity for i in receipt.items] == [1, 1, 1]


@pytest.mark.parametrize(
    "line, name, quantity, unit_cents",
    [
        # before the name
        ("2 ESPRESSO              15.00", "ESPRESSO", 2, None),
        ("2x ESPRESSO             15.00", "ESPRESSO", 2, None),
        ("2 X ESPRESSO            15.00", "ESPRESSO", 2, None),
        ("x2 ESPRESSO             15.00", "ESPRESSO", 2, None),
        ("QTY 2 ESPRESSO          15.00", "ESPRESSO", 2, None),
        ("2 @ 7.50 ESPRESSO       15.00", "ESPRESSO", 2, 750),
        ("2 x $7.50 ESPRESSO      15.00", "ESPRESSO", 2, 750),
        # after the name
        ("ESPRESSO     2          15.00", "ESPRESSO", 2, None),
        ("ESPRESSO 2x             15.00", "ESPRESSO", 2, None),
        ("ESPRESSO x2             15.00", "ESPRESSO", 2, None),
        ("ESPRESSO x 2            15.00", "ESPRESSO", 2, None),
        ("ESPRESSO ×2             15.00", "ESPRESSO", 2, None),
        ("ESPRESSO (2)            15.00", "ESPRESSO", 2, None),
        ("ESPRESSO QTY 2          15.00", "ESPRESSO", 2, None),
        ("ESPRESSO Qty: 2         15.00", "ESPRESSO", 2, None),
        ("ESPRESSO 2 @ 7.50       15.00", "ESPRESSO", 2, 750),
        # both sides: the leading quantity wins and the trailing number stays in the name
        ("2 ESPRESSO 2            15.00", "ESPRESSO 2", 2, None),
        # numbers that are part of the name
        ("2 XL PIZZA              15.00", "XL PIZZA", 2, None),
        ("7 UP                     2.00", "7 UP", 1, None),
        ("3 MUSKETEERS             1.50", "3 MUSKETEERS", 1, None),
    ],
)
def test_quantity_before_or_after_the_name(line, name, quantity, unit_cents):
    [item] = parse_receipt(line).items
    assert (item.name, item.quantity, item.unit_cents) == (name, quantity, unit_cents)
    assert item.amount_cents == 1500 or item.name in ("7 UP", "3 MUSKETEERS")


@pytest.mark.parametrize("quantity_line", ["  2                15.00", "  x2   15.00", "  QTY 2   15.00"])
def test_quantity_only_line_under_the_item_name(quantity_line):
    receipt = parse_receipt(f"ESPRESSO\n{quantity_line}\nBREAD  2.50")
    assert quantities(receipt) == [("ESPRESSO", 2, None, 1500), ("BREAD", 1, None, 250)]


def test_items_without_a_quantity_have_quantity_one():
    assert quantities(parse_receipt("SOUP  5.00")) == [("SOUP", 1, None, 500)]


@pytest.mark.parametrize(
    "line", ["NEW YORK, NY 10021", "Miami, FL 33155", "SPRINGFIELD IL 62704-1234"]
)
def test_us_address_line_is_not_an_item(line):
    assert parse_receipt(line).items == []


@pytest.mark.parametrize("line", ["20% GRAT  $7328.20", "GRAT.  7328.20", "20K GRAT 2 $7328.20"])
def test_grat_abbreviation_is_the_tip(line):
    receipt = parse_receipt(line)
    assert receipt.items == []
    assert receipt.tip_cents == 732820


def test_missing_subtotal_is_none():
    receipt = parse_receipt("Soup 5.00\nTOTAL 5.00")
    assert receipt.subtotal_cents is None
    assert receipt.total_cents == 500


def test_lines_without_a_price_are_ignored():
    receipt = parse_receipt("STORE NAME\n\nThanks for shopping!\nBread $2.50\n")
    assert len(receipt.items) == 1
    assert receipt.items[0].amount_cents == 250


@pytest.mark.parametrize(
    "price, expected",
    [
        ("4.49", 449),
        ("0.01", 1),
        ("0.00", 0),
        ("1,241.02", 124102),
        ("1187", 118700),  # no decimal point: dollars as written, not "fixed"
        ("4.5", 450),  # one decimal digit means tens of cents
        ("$4.49", 449),
        ("  4.49 ", 449),
        ("$ 11.87", 1187),
        ("4,49", 449),  # comma before exactly 2 final digits is a decimal point (OCR)
        ("275,00", 27500),
        ("$15000,00", 1500000),
        ("1,000,00", 100000),
        ("4,499", 449900),  # three digits after the comma: a thousands separator
    ],
)
def test_price_to_cents_valid(price, expected):
    assert price_to_cents(price) == expected


@pytest.mark.parametrize(
    "price",
    ["", "abc", "-1.00", "4.499", "4.", ".49", "4.49.1", "1.2.3", "$", "4,4", "4,4999", "4,49.00"],
)
def test_price_to_cents_rejects_invalid(price):
    with pytest.raises(ValueError, match="not a valid price"):
        price_to_cents(price)


# --- Property tests: random inputs, checked against a rule that must always hold ---

ITEM_WORDS = ["MILK", "BREAD", "RICE 10LB", "Oat milk", "PANEER", "2% YOGURT", "CHIPS #2", "TEA"]


def format_price(cents: int, rng: random.Random) -> str:
    """Write cents the way a receipt might: with or without "$" and thousands commas."""
    dollars = f"{cents // 100:,}" if rng.random() < 0.5 else str(cents // 100)
    dollar_sign = "$" if rng.random() < 0.5 else ""
    return f"{dollar_sign}{dollars}.{cents % 100:02d}"


def test_price_to_cents_round_trips_every_price_up_to_10000_dollars():
    for cents in range(1_000_001):
        assert price_to_cents(f"{cents // 100}.{cents % 100:02d}") == cents
    for cents in range(0, 1_000_001, 997):
        assert price_to_cents(f"${cents // 100:,}.{cents % 100:02d}") == cents


def test_parser_round_trips_random_receipts():
    rng = random.Random(42)  # fixed seed: random but repeatable
    for _ in range(500):
        items = [
            (rng.choice(ITEM_WORDS), rng.randint(1, 500_000)) for _ in range(rng.randint(1, 15))
        ]
        subtotal = sum(cents for _, cents in items)
        tax = rng.randint(0, 5_000)
        lines = ["SYNTHETIC STORE"]
        lines += [f"{name}{' ' * rng.randint(1, 12)}{format_price(c, rng)}" for name, c in items]
        lines += [f"SUBTOTAL  {format_price(subtotal, rng)}", f"TAX  {format_price(tax, rng)}"]
        lines += [f"TOTAL  {format_price(subtotal + tax, rng)}", "THANK YOU"]

        receipt = parse_receipt("\n".join(lines))

        assert [(i.name, i.amount_cents) for i in receipt.items] == items
        assert receipt.subtotal_cents == subtotal
        assert receipt.tax_cents == tax
        assert receipt.total_cents == subtotal + tax
