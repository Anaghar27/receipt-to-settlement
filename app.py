"""FairSplit Streamlit UI. Run with: streamlit run app.py"""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st

from fairsplit.allocate import STRATEGIES
from fairsplit.models import (
    Assignment,
    Receipt,
    format_cents,
    percent_of,
    with_added_tip,
)
from fairsplit.ocr import OcrUnavailable, image_to_text
from fairsplit.parser import parse_receipt, price_to_cents
from fairsplit.settle import describe, settle_receipt
from fairsplit.validate import Issue, can_settle, items_target, resolve_unit_prices, validate

SAMPLES_DIR = Path(__file__).parent / "samples"
SAMPLES = {
    "SYNTHETIC: grocery (valid)": "synthetic_grocery.txt",
    "SYNTHETIC: grocery, misread price": "synthetic_grocery_misread.txt",
    "SYNTHETIC: grocery, missing item": "synthetic_grocery_missing_item.txt",
    "SYNTHETIC: dinner with tip": "synthetic_dinner_with_tip.txt",
}
MOCK_IMAGE_SAMPLE = "synthetic_grocery.txt"
FIXED_COLUMNS = ["Item", "Qty", "Price"]
TIP_PERCENTS = [15, 18, 20]
NO_TIP, CUSTOM_TIP = "No tip", "Custom amount"


def read_sample(file_name: str) -> str:
    return (SAMPLES_DIR / file_name).read_text()


def no_math(text: str) -> str:
    """Escape "$" so Streamlit's Markdown does not render "$1.00 ... $2.00" as a math formula."""
    return text.replace("$", r"\$")


def cents_to_text(cents: int) -> str:
    """118700 -> "1187.00" (editable price text, no "$")."""
    return f"{cents // 100}.{cents % 100:02d}"


# --- Input ---

def load_selected_sample() -> None:
    st.session_state.receipt_text = read_sample(SAMPLES[st.session_state.sample])
    st.session_state.image_source = None


def load_uploaded_image() -> None:
    """Read the photo with local OCR; fall back to the labeled mock if OCR is unavailable."""
    upload = st.session_state.receipt_image
    if upload is None:
        return
    try:
        st.session_state.receipt_text = image_to_text(upload.getvalue())
        st.session_state.image_source = "ocr"
    except OcrUnavailable as reason:
        st.session_state.receipt_text = read_sample(MOCK_IMAGE_SAMPLE)
        st.session_state.image_source = "mock"
        st.session_state.ocr_unavailable = str(reason)


def image_notice() -> None:
    """Say exactly where the receipt text came from, next to the photo for comparison."""
    upload = st.session_state.get("receipt_image")
    if st.session_state.image_source == "ocr":
        st.warning(
            "Text read from your photo with local OCR (Tesseract). OCR makes mistakes: "
            "compare it with the photo and fix or delete lines. FairSplit will not settle "
            "until the receipt adds up.",
            icon="🔎",
        )
    elif st.session_state.image_source == "mock":
        st.info(
            no_math(
                f"MOCK: no OCR was performed. {st.session_state.ocr_unavailable} "
                "Loaded the SYNTHETIC grocery sample instead."
            )
        )
    if upload is not None and st.session_state.image_source:
        with st.expander("Uploaded photo", expanded=st.session_state.image_source == "ocr"):
            st.image(upload.getvalue())


def receipt_input() -> str:
    """Sample picker, photo upload (OCR) and the editable receipt text."""
    if "receipt_text" not in st.session_state:
        st.session_state.receipt_text = read_sample(MOCK_IMAGE_SAMPLE)
        st.session_state.image_source = None  # None (typed/sample) | "ocr" | "mock"
    st.selectbox("Load a sample receipt", list(SAMPLES), key="sample", on_change=load_selected_sample)
    st.file_uploader(
        "Upload a receipt photo. It is read locally with Tesseract OCR "
        "(try samples/synthetic_grocery_photo.png).",
        type=["png", "jpg", "jpeg"],
        key="receipt_image",
        on_change=load_uploaded_image,
    )
    image_notice()
    return st.text_area("Receipt text (edit freely)", key="receipt_text", height=320)


def people_input() -> tuple[list[str], str] | None:
    """Names (2-5, unique) and the payer. Returns None and shows an error if invalid."""
    raw = st.text_input("People (2 to 5, separated by commas)", value="Anagha, Priya, Meera")
    people = [name.strip() for name in raw.split(",") if name.strip()]
    if not 2 <= len(people) <= 5:
        st.error(f"Enter 2 to 5 people. You entered {len(people)}.")
        return None
    if len(set(people)) != len(people):
        st.error("People names must be unique.")
        return None
    if any(name in FIXED_COLUMNS for name in people):
        st.error(f"Names cannot be {', '.join(FIXED_COLUMNS)}.")
        return None
    payer = st.selectbox("Who paid the bill?", people)
    return people, payer


# --- Items table ---

def items_table(receipt: Receipt, people: list[str], text: str) -> pd.DataFrame:
    """Editable items: fix names and prices, tick who shares each item.

    Rows are in receipt order; apply_edits matches them back to items by position,
    so the internal line_no never needs to be shown.
    """
    rows = [
        {"Item": item.name, "Qty": item.quantity, "Price": cents_to_text(item.amount_cents)}
        | {person: True for person in people}
        for item in receipt.items
    ]
    columns = FIXED_COLUMNS + people
    # A new key whenever the text or people change, so stale edits never carry over.
    key = f"items-{hash(text)}-{'|'.join(people)}"
    return st.data_editor(
        pd.DataFrame(rows, columns=columns),
        key=key,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "Qty": st.column_config.NumberColumn("Qty", disabled=True, help="As printed on the receipt"),
            "Price": st.column_config.TextColumn(
                "Line total ($)", help="What this line costs in total. Correct misread prices here."
            ),
            **{person: st.column_config.CheckboxColumn(person) for person in people},
        },
    )


def apply_edits(receipt: Receipt, table: pd.DataFrame, people: list[str]) -> tuple[Receipt, Assignment]:
    """Build the corrected receipt and the assignment from the edited table.

    Raises ValueError if an edited price is not a valid price.
    """
    items, assignment = [], {}
    for original, row in zip(receipt.items, table.to_dict("records")):
        try:
            cents = price_to_cents(str(row["Price"]))
        except ValueError:
            raise ValueError(f"{row['Item']}: {row['Price']!r} is not a valid price.")
        items.append(replace(original, name=str(row["Item"]), amount_cents=cents))
        assignment[original.line_no] = [person for person in people if row[person]]
    return replace(receipt, items=items), assignment  # keeps every other field as parsed


def tip_input(receipt: Receipt) -> int | None:
    """Cents of tip to add on top of the receipt. None (with an error shown) if invalid."""
    if receipt.tip_cents > 0:
        st.caption(no_math(f"Tip printed on the receipt: {format_cents(receipt.tip_cents)} (included)."))
        return 0
    base = items_target(receipt)  # pre-tax amount, even when the subtotal includes tax
    if base is None:
        base = sum(item.amount_cents for item in receipt.items)
    labels = {NO_TIP: NO_TIP, CUSTOM_TIP: CUSTOM_TIP}
    labels |= {f"{p}%": f"{p}% of subtotal ({format_cents(percent_of(base, p))})" for p in TIP_PERCENTS}
    choice = st.radio(
        "Add a tip (not printed on the receipt)",
        [NO_TIP] + [f"{p}%" for p in TIP_PERCENTS] + [CUSTOM_TIP],
        format_func=labels.get,
        horizontal=True,
        key="tip_choice",
    )
    if choice == NO_TIP:
        return 0
    if choice == CUSTOM_TIP:
        text = st.text_input("Tip amount ($)", value="0.00", key="tip_custom")
        try:
            return price_to_cents(text)
        except ValueError:
            st.error(no_math(f"Tip: {text!r} is not a valid amount."))
            return None
    return percent_of(base, int(choice.rstrip("%")))


# --- Validation ---

def show_issue(issue: Issue) -> None:
    if issue.severity == "error":
        st.error(no_math(issue.message), icon="🛑")
    elif issue.severity == "warning":
        st.warning(no_math(issue.message), icon="⚠️")
    else:
        st.info(no_math(issue.message), icon="🔍")
    if issue.hint:
        st.info(no_math(f"Hint: {issue.hint}"), icon="💡")


def receipt_as_read(receipt: Receipt, issues: list[Issue]) -> None:
    """Read-only view of the items with flagged lines highlighted."""
    errors = {i.line_no for i in issues if i.severity == "error" and i.line_no is not None}
    warnings = {i.line_no for i in issues if i.severity == "warning" and i.line_no is not None}
    # Indexed by line_no so rows can be matched to issues; the index is hidden from the user.
    df = pd.DataFrame(
        [{"Item": i.name, "Price": format_cents(i.amount_cents)} for i in receipt.items],
        index=[i.line_no for i in receipt.items],
    )

    def highlight(row: pd.Series) -> list[str]:
        if row.name in errors:
            color = "background-color: rgba(255, 75, 75, 0.25)"
        elif row.name in warnings:
            color = "background-color: rgba(255, 170, 0, 0.30)"
        else:
            color = ""
        return [color] * len(row)

    if not df.empty:
        st.dataframe(df.style.apply(highlight, axis=1), hide_index=True)
    totals = {
        "Subtotal": receipt.subtotal_cents,
        "Tax": receipt.tax_cents,
        "Tip": receipt.tip_cents,
        "Total": receipt.total_cents,
    }
    st.caption(
        no_math(
            "  ·  ".join(f"{k}: {'not found' if v is None else format_cents(v)}" for k, v in totals.items())
        )
    )


def show_validation(receipt: Receipt, issues: list[Issue]) -> None:
    st.subheader("3. Validation")
    if not issues:
        st.success("Receipt reconciles: items = subtotal, and subtotal + tax + tip = total.")
    for issue in sorted(issues, key=lambda i: i.severity != "error"):  # errors first
        show_issue(issue)
    with st.expander("Receipt as read (flagged lines highlighted)", expanded=bool(issues)):
        receipt_as_read(receipt, issues)


# --- Settlement ---

def show_settlement(receipt: Receipt, assignment: Assignment, people: list[str], payer: str) -> None:
    st.subheader("4. Settlement")
    strategy = st.radio("Split tax and tip", list(STRATEGIES), horizontal=True)
    result = settle_receipt(receipt, assignment, people, payer, strategy)
    rows = [
        {
            "Person": p,
            "Pre-tax": format_cents(result.pretax[p]),
            "Tax": format_cents(result.tax[p]),
            "Tip": format_cents(result.tip[p]),
            "Total": format_cents(result.total[p]),
        }
        for p in people
    ]
    rows.append(
        {
            "Person": "Sum",
            "Pre-tax": format_cents(sum(result.pretax.values())),
            "Tax": format_cents(sum(result.tax.values())),
            "Tip": format_cents(sum(result.tip.values())),
            "Total": format_cents(sum(result.total.values())),
        }
    )
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    shares_sum = sum(result.total.values())
    if shares_sum == receipt.total_cents:
        st.success(no_math(f"Shares sum to {format_cents(shares_sum)} = receipt total ✓"))
    else:  # cannot happen: allocate guarantees it. Shown loudly if it ever does.
        st.error(
            no_math(
                f"Shares sum to {format_cents(shares_sum)} but the total is "
                f"{format_cents(receipt.total_cents)}."
            )
        )
    if result.settlements:
        for settlement in result.settlements:
            st.markdown(no_math(f"#### {describe(settlement)}"))
    else:
        st.info(no_math(f"Nobody owes {payer} anything."))


def show_blocked(issues: list[Issue]) -> None:
    st.subheader("4. Settlement")
    count = sum(1 for i in issues if i.severity == "error")
    st.error(
        f"Settlement blocked: the receipt has {count} error(s) above. "
        "FairSplit never settles a receipt that does not reconcile. "
        "Fix the prices in the table, edit the receipt text, or assign every item.",
        icon="🔒",
    )


# --- Page ---

def main() -> None:
    st.set_page_config(page_title="FairSplit", page_icon="🧾", layout="wide")
    st.title("🧾 FairSplit")
    st.caption("Prototype. All sample data is SYNTHETIC. All money math is exact and done in Python.")

    left, right = st.columns([2, 3])
    with left:
        st.subheader("1. Receipt")
        text = receipt_input()
    with right:
        st.subheader("2. People and items")
        people_and_payer = people_input()
        if people_and_payer is None:
            return
        people, payer = people_and_payer
        receipt, quantity_notes = resolve_unit_prices(parse_receipt(text))
        if st.session_state.image_source:
            receipt.source = st.session_state.image_source
        st.caption(
            f"Parsed {len(receipt.items)} items with the rule-based parser (source: {receipt.source}). "
            "Untick people to assign items. To add a missing item, edit the receipt text."
        )
        table = items_table(receipt, people, text)
        added_tip = tip_input(receipt)

    try:
        receipt, assignment = apply_edits(receipt, table, people)
    except ValueError as error:
        st.error(no_math(str(error)), icon="🛑")
        show_blocked([Issue("error", str(error))])
        return
    if added_tip is None:
        show_blocked([Issue("error", "The tip amount is not valid.")])
        return
    receipt = with_added_tip(receipt, added_tip)

    issues = quantity_notes + validate(receipt, assignment)
    show_validation(receipt, issues)
    if can_settle(issues):
        show_settlement(receipt, assignment, people, payer)
    else:
        show_blocked(issues)


main()
