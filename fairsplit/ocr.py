"""Local receipt OCR with Tesseract (free, offline).

Optional: if pytesseract or the Tesseract program is not installed, image_to_text
raises OcrUnavailable and the app falls back to a labeled mock. OCR output is only
text; it goes through the same parser and validation as typed text.
"""

from io import BytesIO

from PIL import Image

from fairsplit.parser import parse_receipt
from fairsplit.validate import can_settle, validate

INSTALL_HELP = "Install it with `brew install tesseract` and `pip install pytesseract`."
# Tesseract can misread small text; photos narrower than this are also read enlarged.
MIN_WIDTH_PX = 700


class OcrUnavailable(RuntimeError):
    """Raised when Tesseract OCR cannot be used on this machine."""


def enlarge_if_small(image: Image.Image) -> Image.Image:
    """Scale a small photo up by a whole factor so it is at least MIN_WIDTH_PX wide."""
    scale = -(-MIN_WIDTH_PX // image.width)  # ceiling division
    if scale <= 1:
        return image
    return image.resize((image.width * scale, image.height * scale), Image.LANCZOS)


def reading_score(text: str) -> tuple[bool, int, int]:
    """How well an OCR reading checks out: (reconciles, summary lines found, items found)."""
    receipt = parse_receipt(text)
    summary_found = [
        receipt.subtotal_cents is not None,
        receipt.tax_cents > 0,
        receipt.tip_cents > 0,
        receipt.total_cents is not None,
    ]
    return can_settle(validate(receipt)), sum(summary_found), len(receipt.items)


def best_reading(readings: list[str]) -> str:
    """The reading with the best score; on a tie, the earliest (the photo as-is)."""
    return max(readings, key=reading_score)


def image_to_text(image_bytes: bytes) -> str:
    """Read the text of a receipt photo. Raises OcrUnavailable if Tesseract is missing.

    A small photo is read twice, as-is and enlarged, because neither is always better;
    the reading that checks out best (see reading_score) is kept.
    """
    try:
        import pytesseract
    except ImportError:
        raise OcrUnavailable(f"pytesseract is not installed. {INSTALL_HELP}") from None
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    candidates = [image]
    if enlarge_if_small(image) is not image:
        candidates.append(enlarge_if_small(image))
    try:
        # --psm 6: treat the image as one block of text, which keeps each receipt line together.
        readings = [pytesseract.image_to_string(c, config="--psm 6") for c in candidates]
    except pytesseract.TesseractNotFoundError:
        raise OcrUnavailable(f"The Tesseract program is not installed. {INSTALL_HELP}") from None
    return best_reading(readings)
