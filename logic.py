"""Pure accounting helpers for the Personal Budget Tracker.

The database stores money as integer paise. Keeping calculations here makes
the rules around imports, balances, and budget warnings easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable


class ValidationError(ValueError):
    """Raised when user supplied accounting data is not valid."""


def parse_paise(value: object, *, allow_zero: bool = False) -> int:
    """Parse a rupee amount into integer paise without float rounding."""
    if value is None:
        raise ValidationError("Amount is required")
    text = str(value).strip().replace(",", "").replace("₹", "")
    if not text:
        raise ValidationError("Amount is required")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValidationError("Enter a valid amount") from error
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ValidationError("Amount must be positive and have at most 2 decimals")
    paise = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    if paise == 0 and not allow_zero:
        raise ValidationError("Amount must be at least ₹0.01")
    return paise


def validate_date(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError) as error:
        raise ValidationError("Date must be a real date in YYYY-MM-DD format") from error
    return parsed.isoformat()


def validate_month(value: object) -> str:
    text = str(value or "").strip()
    try:
        year, month = text.split("-", 1)
        if len(year) != 4 or len(month) != 2 or not 1 <= int(month) <= 12:
            raise ValueError
    except (AttributeError, ValueError) as error:
        raise ValidationError("Month must use YYYY-MM format") from error
    return text


@dataclass(frozen=True)
class LedgerEntry:
    kind: str
    amount_paise: int
    account_id: int
    transfer_account_id: int | None = None


def account_balances(account_ids: Iterable[int], entries: Iterable[LedgerEntry]) -> dict[int, int]:
    """Calculate balances from the ledger, including both sides of transfers."""
    balances = {int(account_id): 0 for account_id in account_ids}
    for entry in entries:
        if entry.account_id not in balances:
            continue
        if entry.kind == "income":
            balances[entry.account_id] += entry.amount_paise
        elif entry.kind == "expense":
            balances[entry.account_id] -= entry.amount_paise
        elif entry.kind == "transfer":
            balances[entry.account_id] -= entry.amount_paise
            if entry.transfer_account_id in balances:
                balances[entry.transfer_account_id] += entry.amount_paise
    return balances


def budget_progress(limit_paise: int, spent_paise: int) -> dict[str, int | str | bool]:
    """Return UI-friendly progress and the warning level for a category budget."""
    limit = max(0, int(limit_paise))
    spent = max(0, int(spent_paise))
    # Floor the display percentage so 79.99% does not trigger the 80% warning.
    percent = 0 if limit == 0 else int(spent / limit * 100)
    level = "safe"
    if percent >= 100:
        level = "over"
    elif percent >= 80:
        level = "warning"
    return {"spent_paise": spent, "limit_paise": limit, "percent": percent, "level": level, "warn": level != "safe"}


def validate_transaction_kind(value: object) -> str:
    kind = str(value or "").strip().lower()
    if kind not in {"income", "expense", "transfer"}:
        raise ValidationError("Choose income, expense, or transfer")
    return kind
