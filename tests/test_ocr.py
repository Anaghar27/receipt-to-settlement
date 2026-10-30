import shutil
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from fairsplit.ocr import (
    MIN_WIDTH_PX,
    OcrUnavailable,
    best_reading,
    enlarge_if_small,
    image_to_text,
)
from fairsplit.parser import parse_receipt
from fairsplit.validate import validate

SAMPLES = Path(__file__).parent.parent / "samples"
needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="Tesseract is not installed"
)


def render_receipt(text: str) -> bytes:
    """Draw receipt text as a clean PNG: names on the left, prices in a right column."""
    font = ImageFont.load_default(size=28)
    lines = text.splitlines()
    image = Image.new("L", (720, 60 + 40 * len(lines)), 255)
    draw = ImageDraw.Draw(image)
    for n, line in enumerate(lines):
        name, _, price = line.rpartition(" ")
        draw.text((30, 30 + 40 * n), name.strip() if price else line, font=font, fill=0)
        if price:
            draw.text((560, 30 + 40 * n), price, font=font, fill=0)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def summary(receipt):
    items = [(i.name, i.amount_cents) for i in receipt.items]
    return items, receipt.subtotal_cents, receipt.tax_cents, receipt.tip_cents, receipt.total_cents


@needs_tesseract
def test_sample_photo_ocr_matches_the_text_sample():
    from_photo = parse_receipt(image_to_text((SAMPLES / "synthetic_grocery_photo.png").read_bytes()))
    from_text = parse_receipt((SAMPLES / "synthetic_grocery.txt").read_text())
    assert summary(from_photo) == summary(from_text)
    assert validate(from_photo) == []


@needs_tesseract
def test_rendered_dinner_receipt_round_trips_through_ocr():
    text = (SAMPLES / "synthetic_dinner_with_tip.txt").read_text()
    from_photo = parse_receipt(image_to_text(render_receipt(text)))
    assert summary(from_photo) == summary(parse_receipt(text))


def test_missing_pytesseract_raises_ocr_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)  # makes "import pytesseract" fail
    with pytest.raises(OcrUnavailable, match="pytesseract is not installed"):
        image_to_text(render_receipt("SOUP 5.00"))


def test_missing_tesseract_program_raises_ocr_unavailable(monkeypatch):
    pytesseract = pytest.importorskip("pytesseract")
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "/nonexistent/tesseract")
    with pytest.raises(OcrUnavailable, match="Tesseract program is not installed"):
        image_to_text(render_receipt("SOUP 5.00"))


def test_small_photos_are_enlarged_by_a_whole_factor():
    small = Image.new("RGB", (233, 590), "white")
    assert enlarge_if_small(small).size == (233 * 4, 590 * 4)  # 4 x 233 = 932 >= 700
    wide = Image.new("RGB", (MIN_WIDTH_PX, 100), "white")
    assert enlarge_if_small(wide) is wide


@needs_tesseract
def test_small_rendered_receipt_still_reads_correctly():
    text = (SAMPLES / "synthetic_grocery.txt").read_text()
    image = Image.open(BytesIO(render_receipt(text)))
    small = image.resize((image.width // 3, image.height // 3))
    buffer = BytesIO()
    small.save(buffer, format="PNG")
    assert summary(parse_receipt(image_to_text(buffer.getvalue()))) == summary(parse_receipt(text))


def test_best_reading_prefers_one_that_reconciles():
    garbled = "MILK 4.49\nEGGS 3344.00\nSUBTOTAL 5.69"
    clean = "MILK 4.49\nEGGS 3.99\nSUBTOTAL 8.48\nTOTAL 8.48"
    assert best_reading([garbled, clean]) == clean


def test_best_reading_prefers_more_summary_lines_found():
    with_totals = "SOUP 5.00\nFISH 9.00\nTIP 1.00\nTOTAL 99.00"  # does not reconcile
    without = "SOUP 5.00\nFISH 9.00\nBREAD 2.00"
    assert best_reading([without, with_totals]) == with_totals


def test_best_reading_keeps_the_first_on_a_tie():
    assert best_reading(["SOUP 5.00", "FISH 5.00"]) == "SOUP 5.00"
