# FairSplit

Turn an itemized receipt into a fair, verified settlement between people.
Prototype for MiraFlores AI, Case Study 1.

## Problem and what the prototype does

When friends share a bill, someone has to read the receipt, work out who had what, split
shared items, add each person's part of the tax and tip, and say who owes whom. Doing this by
hand is slow and easy to get wrong, and receipts themselves are often misread: a price missing
its decimal point (`1187` instead of `11.87`), an item that did not print, or a scanned line the
OCR garbled. FairSplit takes a receipt (pasted text or a photo), parses its line items, lets you
assign each item to one or more people, and **checks that the receipt adds up to the cent before
it settles anything**. If the numbers do not reconcile, it refuses to settle and points at the
likely problem line. If they do, it splits shared items equally, allocates tax and tip
proportionally (or equally), rounds so the shares sum exactly to the receipt total, and prints
the settlement, for example `Priya pays Anagha $28.70`.

## Setup and run

Requires Python 3.10+ (developed on 3.13).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

streamlit run app.py      # the app, at http://localhost:8501
pytest -q                 # the tests (169 pass)
```

**Optional: real photo OCR.** Photo upload reads receipts with Tesseract, locally and for free.
It needs the Tesseract program as well as the Python package:

```bash
brew install tesseract    # macOS; on Ubuntu: sudo apt install tesseract-ocr
```

Without Tesseract the app still runs: photo upload falls back to a clearly labeled **mock** that
loads the synthetic grocery sample, and the OCR tests are skipped.

No API key and no paid service is needed for anything.

## Dependencies

| Package | Why | Required? |
|---|---|---|
| `streamlit` | the UI (also installs `pandas` and `Pillow`, which the app uses) | yes |
| `pytest` | tests | yes, for tests |
| `pytesseract` + the `tesseract` program | local OCR of receipt photos | optional |

Developed with Python 3.13.3, Streamlit 1.66.1, pytest 9.2.0, pytesseract 0.3.13,
Tesseract 5.5.4.

## How it works

```
receipt text or photo
  -> (photo) local OCR: text only, goes through the same steps as typed text
  -> parser.py     line items, quantities, subtotal / tax / tip / total, read as written
  -> validate.py   reconcile to the cent; flag suspicious lines; hints; block on any error
  -> allocate.py   exact Fraction shares; tax and tip proportional or equal;
                   largest-remainder rounding so shares sum exactly to the total
  -> settle.py     "X pays Y $Z" for everyone except the payer
```

Design rules the code follows:

- **No floats for money.** Everything is integer cents or `fractions.Fraction`, and rounding happens
  once, at the end.
- **Shares always sum exactly to the receipt total.** Largest-remainder rounding: floor each
  person's exact total, then give the leftover cents to the largest fractional remainders, ties
  to whoever was entered first. Checked by property tests over thousands of random receipts.
- **Never settle an unreconciled receipt.** `settle_receipt` runs validation itself and raises if
  there is any error, so even a UI bug cannot settle a bad receipt.
- **The parser reads amounts as written and never silently fixes them.** Validation explains what
  is wrong and suggests a fix, and the user makes the change in the table or the text.
- **No LLM does arithmetic.** There is no LLM in the current build at all.

## Using the app

1. **Receipt** (left): pick a SYNTHETIC sample, upload a photo, or paste and edit text.
2. **People and items** (right): enter 2 to 5 names and the payer. Every item starts ticked for
   everyone (shared), and you untick people to assign it. Correct misread prices in the *Line total* column.
   Optionally add a tip that is not printed on the receipt (15 / 18 / 20% of the pre-tax amount, or
   a custom amount).
3. **Validation**: errors in red, warnings in amber, notes in blue, hints underneath. Flagged
   lines are highlighted in *Receipt as read*.
4. **Settlement**: choose *proportional* or *equal* for tax and tip, then see the per-person
   breakdown, "Shares sum to $X = receipt total ✓", and who pays whom. If there is any error,
   settlement is blocked with the reason.

## Sample data and expected outputs

All sample data is **synthetic** and labeled so in the file names, the file contents and the UI.

### `samples/synthetic_grocery.txt` (valid)

```
GROCERY RECEIPT (SYNTHETIC DATA)
MILK 2% GAL            4.49
EGGS DOZEN             3.99
BASMATI RICE 10LB     14.99
GREEK YOGURT           5.49
CHICKEN BREAST        11.87
SPINACH                3.29
OLIVE OIL              9.99
OAT MILK               4.79
PANEER                 6.99
SUBTOTAL              65.89
TAX                    2.10
TOTAL                 67.99
```

People: Anagha, Priya, Meera. Payer: Anagha. Assignment: milk, eggs, rice and olive oil shared
by all three, Greek yogurt and spinach to Anagha, chicken breast and oat milk to Priya, and paneer
to Meera. (In the app, untick the other two people on those five rows.)

| Person | Pre-tax | Tax (proportional) | Total | Total with `equal` tax |
|--------|--------:|-------------------:|------:|-----------------------:|
| Anagha | $19.94 | $0.63 | **$20.57** | $20.64 |
| Priya  | $27.81 | $0.89 | **$28.70** | $28.51 |
| Meera  | $18.14 | $0.58 | **$18.72** | $18.84 |
| Sum    | $65.89 | $2.10 | **$67.99** | $67.99 |

Settlement: `Priya pays Anagha $28.70`, `Meera pays Anagha $18.72`.
Exact totals before rounding are 2056.86 / 2869.98 / 1872.16 cents. The floors sum to 6797, so
the 2 leftover cents go to Priya and Anagha, who have the largest remainders.

### `samples/synthetic_grocery_misread.txt`

Same receipt, but the chicken line reads `CHICKEN BREAST  1187`.
Expected: the error *"Items sum to $1,241.02 but the subtotal is $65.89 (mismatch $1,175.13)."*,
the warning *"CHICKEN BREAST looks suspicious at $1,187.00."* with the hint *"CHICKEN BREAST may be
$11.87, not $1,187.00. That would reconcile the receipt."*, and no settlement. Typing `11.87` in
the table settles it to the numbers above.

### `samples/synthetic_grocery_missing_item.txt`

Same receipt without the PANEER line.
Expected: error *"Items sum to $58.90 but the subtotal is $65.89 (mismatch $6.99)."* with the
hint *"An item worth $6.99 may be missing or unreadable."*, and no settlement.

### `samples/synthetic_dinner_with_tip.txt`

Six items ($61.20), tax $5.43 and an 18% tip of $11.02, total $77.65. With Margherita pizza
shared by Anagha and Priya, Caesar salad to Anagha, pad thai to Priya, garlic naan shared by all
three, mango lassi to Meera and tiramisu shared by Priya and Meera, proportional allocation gives
**$26.65 / $36.76 / $14.24 = $77.65**, with the tip split $3.78 / $5.22 / $2.02 = $11.02.

### `samples/synthetic_grocery_photo.png`

The grocery receipt drawn as an image. Upload it to see OCR work end to end: it reads back to
exactly the text sample and reconciles.

## Implemented, mocked, deferred

**Implemented**

- Rule-based parser for messy text:
  - summary lines matched on any word ("Total Taxes" is tax, "Grand Total" or "Amount Due" is the
    total, and "GRAT" or "Gratuity" is the tip)
  - several tax lines added up
  - address lines and rows of suggested tips ignored
  - OCR comma decimals (`275,00`)
- Quantities before or after the name (`2 ESPRESSO`, `ESPRESSO x2`, `(2)`, `QTY 2`), with a unit
  price (`2 @ 4.79`), or on the line below the name. The amount is always the line total.
- Validation:
  - items vs subtotal and subtotal + tax + tip vs total, to the cent
  - receipts whose subtotal already includes tax
  - a subtotal, tax or tip larger than the total, quoting the misread lines
  - unassigned items
  - suspicious prices (over 5× the median or over $200)
  - quantity × unit price vs the line total
- Hints and notes:
  - "$11.87 would reconcile"
  - "an item worth $X may be missing"
  - reading `TACOS 3 4.00` as 3 × $4.00, only when that is the single reading that reconciles,
    and always reporting what was checked
- Allocation with swappable strategies (`STRATEGIES = {"proportional": ..., "equal": ...}`),
  exact `Fraction` math and largest-remainder rounding.
- Settlement for one payer. People who owe $0 are not listed.
- Tip added in the app when it is not printed on the receipt.
- Local photo OCR with Tesseract (photos under 700 px wide are read as-is and enlarged, and the
  reading that checks out best is kept).
- Streamlit UI and 169 tests: parser, validation, allocation (including rounding property tests),
  settlement and OCR.

**Mocked**

- Photo upload when Tesseract is not installed: it loads the synthetic grocery sample and says so.

**Deferred (out of scope)**

- An optional LLM parser (`parser_llm.py`).
- Multiple bills, multiple currencies, payment-app integration, accounts, databases, deployment.

## Known limitations

- OCR on small or blurry photos needs manual correction. Validation catches the misreads and
  blocks settlement, but the user has to fix the text.
- Uploading a photo replaces whatever is in the receipt text box, including edits.
- The "over $200" warning also fires on genuinely expensive items (for example fine wine) even
  when the receipt reconciles. Warnings never block settlement.
- A product name that starts with a number (`7 UP`) can be read as a quantity unless it is on a
  short built-in list. The money is unaffected. Only the name and the Qty column are.
- Quantities over 99 before the name (`120 NAPKINS`) are not recognized as quantities.
- Splits are equal among the people ticked, so you cannot give 2 of 3 units to one person.

## Time spent and what I would investigate next

**Time spent:** _to fill in_ (time box: 4 hours).

**Next:**

1. **Optional LLM parser** (Claude) for messy and photographed receipts. It would return the same
   `Receipt` schema as strict JSON, go through the same validation, never do arithmetic, and fall
   back to the rule-based parser on any error.
2. **Better OCR input:** deskew and crop photos, and keep the user's text edits when a photo is
   re-uploaded.
3. **Quieter warnings on reconciled receipts:** one summary note instead of a warning per
   expensive item.
4. **Uneven splits:** shares by unit or by percentage, not only equal among the people ticked.
5. **A UI test in the suite.** The app is checked with Streamlit's `AppTest` from scripts outside
   the repo. Making those part of `pytest` would protect the UI too.

## How AI was used

I built this with Claude Code as a pair programmer, one milestone at a time, reviewing each one
before moving on. The AI wrote the code and tests, and I checked every acceptance number by hand, ran
the app myself, and tested it on real receipt photos, which found several issues the AI's own
checks had missed:

- money amounts rendering as math formulas
- a quantity check that was silently skipped
- receipts whose subtotal includes tax

Some fixes the AI proposed were measured and rejected or reworked. For example, always enlarging
small photos made OCR worse on a real receipt. [AI_USAGE.md](AI_USAGE.md) logs every file the AI
generated, how it was verified, and every mistake that was caught.

## Project structure

```
app.py                  Streamlit UI
fairsplit/
  models.py             LineItem, Receipt, Result; cents formatting; added tip
  parser.py             rule-based text parser
  ocr.py                optional local OCR (Tesseract)
  validate.py           reconciliation, suspicious lines, hints, quantity readings
  allocate.py           strategies and largest-remainder rounding
  settle.py             who pays whom; refuses unreconciled receipts
samples/                synthetic receipts (text and one photo)
tests/                  pytest suite
AI_USAGE.md             what the AI generated and how it was checked
CLAUDE.md               the build spec
```
