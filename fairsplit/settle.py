"""Who pays whom. Refuses to settle a receipt that does not reconcile."""

from fairsplit.allocate import allocate
from fairsplit.models import Assignment, Receipt, Result, format_cents
from fairsplit.validate import validate


class UnreconciledReceiptError(ValueError):
    """Raised when asked to settle a receipt that has validation errors."""


def settlements(totals: dict[str, int], payer: str) -> list[tuple[str, str, int]]:
    """Everyone except the payer pays the payer their total. Zero amounts are skipped."""
    return [(person, payer, cents) for person, cents in totals.items() if person != payer and cents > 0]


def describe(settlement: tuple[str, str, int]) -> str:
    """("Priya", "Anagha", 2870) -> "Priya pays Anagha $28.70"."""
    from_person, to_person, cents = settlement
    return f"{from_person} pays {to_person} {format_cents(cents)}"


def settle_receipt(
    receipt: Receipt,
    assignment: Assignment,
    people: list[str],
    payer: str,
    strategy: str = "proportional",
) -> Result:
    """Validate, allocate and settle. Raises UnreconciledReceiptError if validation has errors."""
    if payer not in people:
        raise ValueError(f"Payer {payer!r} is not in the people list.")
    errors = [issue.message for issue in validate(receipt, assignment) if issue.severity == "error"]
    if errors:
        raise UnreconciledReceiptError("Cannot settle: " + " ".join(errors))
    shares = allocate(receipt, assignment, people, strategy)
    return Result(
        pretax=shares["pretax"],
        tax=shares["tax"],
        tip=shares["tip"],
        total=shares["total"],
        settlements=settlements(shares["total"], payer),
    )
