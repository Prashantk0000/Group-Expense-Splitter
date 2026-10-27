"""Pure money and settlement rules for Group Expense Splitter.

All amounts are integer paise. The functions in this module deliberately do not
know anything about SQLite or HTTP, which keeps the hard accounting rules easy
to test and safe to reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Iterable, Sequence


class ValidationError(ValueError):
    """Raised when user supplied expense data is invalid."""


def parse_paise(value: object, *, allow_zero: bool = False) -> int:
    """Parse rupees into integer paise without going through a float."""
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
    paise = int(amount * 100)
    if paise == 0 and not allow_zero:
        raise ValidationError("Amount must be at least ₹0.01")
    return paise


def parse_percentage(value: object) -> Decimal:
    try:
        percentage = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as error:
        raise ValidationError("Enter a valid percentage") from error
    if not percentage.is_finite() or percentage < 0 or percentage > 100:
        raise ValidationError("Percentages must be between 0 and 100")
    return percentage


def validate_date(value: object) -> str:
    from datetime import date

    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError) as error:
        raise ValidationError("Date must be a real date in YYYY-MM-DD format") from error
    return parsed.isoformat()


def valid_split_type(value: object) -> str:
    split_type = str(value or "equal").strip().lower()
    if split_type not in {"equal", "exact", "percentage"}:
        raise ValidationError("Choose equal, exact, or percentage split")
    return split_type


def _validate_members(member_ids: Iterable[int], shares: Sequence[tuple[int, int]]) -> list[int]:
    members = [int(member_id) for member_id in member_ids]
    if not members:
        raise ValidationError("Add at least one member to the split")
    ids = [member_id for member_id, _ in shares]
    if len(ids) != len(set(ids)):
        raise ValidationError("Each member can appear only once in a split")
    if set(ids) != set(members):
        raise ValidationError("The split must include every group member exactly once")
    return members


def split_expense(
    total_paise: int,
    member_ids: Sequence[int],
    split_type: str = "equal",
    values: Sequence[object] | None = None,
) -> dict[int, int]:
    """Return exact paise shares for equal, exact, or percentage splits.

    Equal splits give the first members one extra paise when needed. Percentage
    splits round down first and assign the remainder to the last member, so the
    stored shares always add up to the entered total.
    """
    total = int(total_paise)
    if total <= 0:
        raise ValidationError("Amount must be at least ₹0.01")
    members = [int(member_id) for member_id in member_ids]
    if not members or len(members) != len(set(members)):
        raise ValidationError("A split needs unique group members")
    kind = valid_split_type(split_type)
    raw = list(values or [])

    if kind == "equal":
        base, remainder = divmod(total, len(members))
        return {member_id: base + (1 if index < remainder else 0) for index, member_id in enumerate(members)}

    if len(raw) != len(members):
        raise ValidationError("Enter a split value for every member")

    if kind == "exact":
        shares = [parse_paise(value, allow_zero=True) for value in raw]
        if sum(shares) != total:
            raise ValidationError("Exact shares must add up to the full expense")
        return dict(zip(members, shares))

    percentages = [parse_percentage(value) for value in raw]
    if sum(percentages) != Decimal("100"):
        raise ValidationError("Percentages must add up to 100")
    shares = [int((Decimal(total) * percentage / Decimal(100)).to_integral_value(rounding=ROUND_DOWN)) for percentage in percentages]
    shares[-1] += total - sum(shares)
    return dict(zip(members, shares))


@dataclass(frozen=True)
class Balance:
    user_id: int
    net_paise: int


def calculate_balances(
    member_ids: Iterable[int],
    expenses: Iterable[tuple[int, int, int]],
    payments: Iterable[tuple[int, int, int]],
) -> dict[int, int]:
    """Calculate net balances: positive means the group owes that person."""
    balances = {int(user_id): 0 for user_id in member_ids}
    for paid_by, amount_paise, paid_for_paise in expenses:
        if paid_by not in balances:
            continue
        balances[paid_by] += int(amount_paise)
        for user_id, share in paid_for_paise.items() if isinstance(paid_for_paise, dict) else []:
            if user_id in balances:
                balances[user_id] -= int(share)
    for payer, payee, amount_paise in payments:
        if payer in balances:
            balances[payer] += int(amount_paise)
        if payee in balances:
            balances[payee] -= int(amount_paise)
    return balances


def _greedy_settlement(balances: dict[int, int]) -> list[dict[str, int]]:
    debtors = [[user_id, -amount] for user_id, amount in balances.items() if amount < 0]
    creditors = [[user_id, amount] for user_id, amount in balances.items() if amount > 0]
    debtors.sort(key=lambda item: item[1], reverse=True)
    creditors.sort(key=lambda item: item[1], reverse=True)
    result: list[dict[str, int]] = []
    debtor_index = creditor_index = 0
    while debtor_index < len(debtors) and creditor_index < len(creditors):
        debtor, owed = debtors[debtor_index]
        creditor, due = creditors[creditor_index]
        amount = min(owed, due)
        result.append({"from_user_id": debtor, "to_user_id": creditor, "amount_paise": amount})
        debtors[debtor_index][1] -= amount
        creditors[creditor_index][1] -= amount
        if debtors[debtor_index][1] == 0:
            debtor_index += 1
        if creditors[creditor_index][1] == 0:
            creditor_index += 1
    return result


def settle_up(balances: dict[int, int]) -> list[dict[str, int]]:
    """Find an exact minimum-payment settlement for a small group.

    The depth-first search cancels one debtor with one creditor at a time and
    skips equivalent balances. For unusually large groups it uses the same
    correct greedy result with near-minimum payment count.
    """
    clean = {int(user_id): int(amount) for user_id, amount in balances.items() if int(amount)}
    if not clean:
        return []
    if len(clean) > 12:
        return _greedy_settlement(clean)
    users = list(clean)
    amounts = [clean[user_id] for user_id in users]
    best: list[tuple[int, int, int]] | None = None

    def search(start: int, payments: list[tuple[int, int, int]]) -> None:
        nonlocal best
        while start < len(amounts) and amounts[start] == 0:
            start += 1
        if start == len(amounts):
            if best is None or len(payments) < len(best):
                best = payments.copy()
            return
        if best is not None and len(payments) >= len(best):
            return
        seen: set[int] = set()
        for index in range(start + 1, len(amounts)):
            if amounts[start] * amounts[index] >= 0 or amounts[index] in seen:
                continue
            seen.add(amounts[index])
            first, second = amounts[start], amounts[index]
            amount = min(abs(first), abs(second))
            if first < 0:
                transfer = (users[start], users[index], amount)
            else:
                transfer = (users[index], users[start], amount)
            amounts[index] += amounts[start]
            amounts[start] = 0
            search(start + 1, payments + [transfer])
            amounts[start] = first
            amounts[index] = second

    search(0, [])
    return [
        {"from_user_id": payer, "to_user_id": payee, "amount_paise": amount}
        for payer, payee, amount in (best or [])
    ]
