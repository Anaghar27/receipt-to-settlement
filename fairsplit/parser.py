"""Rule-based receipt text parser (the default; needs no API key)."""

import re

from fairsplit.models import LineItem, Receipt

# A price: digits with optional thousands commas ("1,241"), then optionally 1-2 decimals.
# A comma before exactly two final digits ("275,00", a common OCR misread) can only be a
# decimal point, because thousands groups have three digits.
# No decimal point ("1187") is allowed and means whole dollars as written.
PRICE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2}|,\d{2})?"
COMMA_DECIMAL = re.compile(r",(\d{2})$")
VALID_PRICE = re.compile(PRICE)

# A label, at least one space, an optional "$", then a price at the end of the line.
PRICE_LINE = re.compile(rf"^(?P<label>.*?\S)\s+\$?\s*(?P<price>{PRICE})\s*$")

# Summary lines fill a Receipt field instead of being an item. Checked in this order, so
# "TOTAL TAXES" is tax and "SUB TOTAL" is the subtotal, not the total.
SUMMARY_RULES = [
    ("subtotal_cents", {"SUBTOTAL"}, ["SUB TOTAL"]),
    ("tax_cents", {"TAX", "TAXES"}, []),
    ("tip_cents", {"TIP", "GRATUITY", "GRAT"}, []),
    ("total_cents", {"TOTAL"}, ["AMOUNT DUE", "BALANCE DUE"]),
]
ADDED_UP_FIELDS = {"tax_cents", "tip_cents"}  # e.g. state tax + city tax

# Quantities. A quantity can sit before or after the name, with an optional unit price.
# "x" and "×" mean "times"; "QTY" and brackets are labels around the number.
TIMES = r"(?:x|×)"
UNIT = rf"(?P<qty>\d{{1,3}})\s*(?:@|{TIMES})\s*\$?(?P<unit>{PRICE})(?:\s*(?:ea|each))?"
# After the name, with a unit price: "OAT MILK 2 @ 4.79", "3 x $0.25 ea".
TRAILING_UNIT_PRICE = re.compile(rf"(?:^|\s){UNIT}$", re.IGNORECASE)
# Before the name, with a unit price: "2 @ 7.50 ESPRESSO".
LEADING_UNIT_PRICE = re.compile(rf"^{UNIT}\s+(?=[A-Za-z])", re.IGNORECASE)
# Before the name: "2 ESPRESSO", "2x ESPRESSO", "2 X ESPRESSO", "x2 ESPRESSO", "QTY 2 ESPRESSO".
# Not "2% MILK" or "10LB RICE": the number must be followed by a space (or an "x" and a space).
LEADING_QUANTITY = re.compile(
    rf"^(?:(?:qty\s*:?|{TIMES})\s*)?(?P<qty>\d{{1,2}})(?:\s*{TIMES})?\s+(?=[A-Za-z])",
    re.IGNORECASE,
)
# After the name: "ESPRESSO 2", "ESPRESSO 2x", "ESPRESSO x2", "ESPRESSO x 2", "(2)", "QTY: 2".
TRAILING_QUANTITY = re.compile(
    rf"\s+(?:\(\s*(?P<bracket>\d{{1,3}})\s*\)|(?:qty\s*:?|{TIMES})\s*(?P<marked>\d{{1,3}})"
    rf"|(?P<bare>\d{{1,2}})(?:\s*{TIMES})?)$",
    re.IGNORECASE,
)
# A line that is only a quantity, under its item name: "BANANAS" then "  2   1.50".
QUANTITY_ONLY = re.compile(rf"^(?:(?:qty\s*:?|{TIMES})\s*)?(?P<qty>\d{{1,3}})(?:\s*{TIMES})?$", re.IGNORECASE)
# Product names that start with a number that is not a quantity.
NUMBER_NAMES = ("7 UP", "3 MUSKETEERS", "5 HOUR ENERGY")
# A US address line ending in "STATE ZIP": the zip code is not a price.
US_ADDRESS = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?$")


def has_letters(text: str) -> bool:
    """A line without letters ("$7.43 $9.90", a row of suggested tips) has no name, so it
    is not an item on its own."""
    return re.search(r"[A-Za-z]", text) is not None


def split_quantity(label: str) -> tuple[str, int, int | None]:
    """Split a label into (name, quantity, unit_cents). The amount on the line is always
    the line total; the quantity is for display and for validation's checks.

    "OAT MILK 2 @ 4.79" -> ("OAT MILK", 2, 479)    after the name, with a unit price
    "2 @ 7.50 ESPRESSO" -> ("ESPRESSO", 2, 750)    before the name, with a unit price
    "2x ESPRESSO"       -> ("ESPRESSO", 2, None)   before the name
    "ESPRESSO (2)"      -> ("ESPRESSO", 2, None)   after the name
    "5 TAWNY 40"        -> ("TAWNY 40", 5, None)   before wins, so "40" stays in the name
    "3 @ 0.25"          -> ("", 3, 25)             only a quantity: the name is on the line above
    "7 UP"              -> ("7 UP", 1, None)       a product name, not 7 of "UP"
    "SOUP"              -> ("SOUP", 1, None)
    """
    if label.upper().startswith(NUMBER_NAMES):
        return label, 1, None
    if match := QUANTITY_ONLY.fullmatch(label):
        return "", int(match["qty"]), None
    if match := TRAILING_UNIT_PRICE.search(label):
        return label[: match.start()].strip(), int(match["qty"]), price_to_cents(match["unit"])
    if match := LEADING_UNIT_PRICE.search(label):
        return label[match.end() :].strip(), int(match["qty"]), price_to_cents(match["unit"])
    if match := LEADING_QUANTITY.search(label):
        return label[match.end() :].strip(), int(match["qty"]), None
    if match := TRAILING_QUANTITY.search(label):
        quantity = match["bracket"] or match["marked"] or match["bare"]
        return label[: match.start()].strip(), int(quantity), None
    return label, 1, None


def price_to_cents(price: str) -> int:
    """Convert a price string like "4.49", "$1,241.02", "4.5" or "1187" to integer cents.

    Uses integer arithmetic only (no floats). A price with no decimal point is read
    as whole dollars, exactly as written ("1187" -> 118700); validation is responsible
    for catching misreads like that. Raises ValueError for anything that is not a price.
    """
    cleaned = price.strip().removeprefix("$").strip()
    if not VALID_PRICE.fullmatch(cleaned):
        raise ValueError(f"{price!r} is not a valid price")
    cleaned = COMMA_DECIMAL.sub(r".\1", cleaned)  # "275,00" -> "275.00"
    dollars, _, cents = cleaned.replace(",", "").partition(".")
    return int(dollars) * 100 + int(cents.ljust(2, "0"))


def summary_field(label: str) -> str | None:
    """Return the Receipt field name if the label is a summary line, else None.

    Matches whole words, case-insensitive: "Total Taxes" -> tax, "Grand Total" -> total,
    "TIP 18%" -> tip, but "TIPSY CAKE" is an item.
    """
    words = re.findall(r"[A-Z]+", label.upper())
    phrase = " ".join(words)
    for field_name, keywords, phrases in SUMMARY_RULES:
        if keywords & set(words) or any(p in phrase for p in phrases):
            return field_name
    return None


def parse_receipt(text: str) -> Receipt:
    """Parse raw receipt text into a Receipt.

    Lines without a trailing price (headers, footers, blank lines) are ignored.
    The amount at the end of an item line is always its line total, read as written.
    A quantity line under its name ("BANANAS" then "3 @ 0.25   0.75", or "2   1.50")
    is joined to it.
    Several tax or tip lines are added up. If the subtotal or total is printed more
    than once ("Grand Total", then "Amount Due"), the first one is kept.
    """
    receipt = Receipt(source="rule")
    previous_line = ""  # the line just before, if it had no price
    for line_no, raw in enumerate(text.splitlines(), start=1):
        match = PRICE_LINE.match(raw.strip())
        if not match or US_ADDRESS.search(raw.strip()):
            previous_line = raw
            continue
        label = match.group("label").strip()
        cents = price_to_cents(match.group("price"))
        field_name = summary_field(label)
        if field_name is None:
            name, quantity, unit_cents = split_quantity(label)
            if not name and has_letters(previous_line):  # quantity line under its name
                name, raw = previous_line.strip(), f"{previous_line}\n{raw}"
            if has_letters(name):
                receipt.items.append(LineItem(name, cents, line_no, raw, quantity, unit_cents))
        elif field_name in ADDED_UP_FIELDS:
            setattr(receipt, field_name, getattr(receipt, field_name) + cents)
            receipt.summary_raw.setdefault(field_name, raw.strip())
        elif getattr(receipt, field_name) is None:
            setattr(receipt, field_name, cents)
            receipt.summary_raw[field_name] = raw.strip()
        previous_line = ""
    return receipt
