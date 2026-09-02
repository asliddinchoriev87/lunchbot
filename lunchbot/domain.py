from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class ParsedMenu:
    menu_date: date
    items: tuple[str, ...]
    portion_price: int
    delivery_fee: int = 0
    free_delivery_min: int | None = None
    raw_text: str = ""


@dataclass(frozen=True)
class ReceiptAnalysis:
    amount: int | None
    recipient_name: str | None
    transaction_time: str | None
    success_visible: bool
    confidence: float
    note: str = ""


@dataclass(frozen=True)
class PaymentDecision:
    status: str
    amount_matches: bool
    recipient_matches: bool
    reason: str


@dataclass
class OrderSummary:
    menu_id: int
    menu_date: str
    portion_price: int
    delivery_fee: int
    free_delivery_min: int | None
    rows: list[dict] = field(default_factory=list)

    @property
    def portion_count(self) -> int:
        return len(self.rows)

    @property
    def food_total(self) -> int:
        return sum(int(row["price"]) for row in self.rows)

    @property
    def applied_delivery_fee(self) -> int:
        if self.free_delivery_min and self.portion_count >= self.free_delivery_min:
            return 0
        return self.delivery_fee

    @property
    def grand_total(self) -> int:
        return self.food_total + self.applied_delivery_fee


def normalize_person_name(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def evaluate_payment(
    analysis: ReceiptAnalysis,
    expected_amount: int,
    accepted_recipients: tuple[str, ...],
    expected_date: date | None = None,
    minimum_confidence: float = 0.80,
) -> PaymentDecision:
    amount_matches = analysis.amount == expected_amount
    normalized = normalize_person_name(analysis.recipient_name or "")
    recipient_matches = bool(accepted_recipients) and any(
        normalize_person_name(name) in normalized
        or normalized in normalize_person_name(name)
        for name in accepted_recipients
        if normalized
    )

    if not analysis.success_visible:
        return PaymentDecision(
            "needs_review", amount_matches, recipient_matches, "Success status is not visible"
        )
    if not amount_matches:
        return PaymentDecision(
            "needs_review", False, recipient_matches, "Transferred amount does not match the order"
        )
    if not accepted_recipients:
        return PaymentDecision(
            "needs_review", amount_matches, False, "No payment recipients are configured"
        )
    if not recipient_matches:
        return PaymentDecision(
            "needs_review", True, False, "Recipient does not match the configured recipients"
        )
    if expected_date:
        if not analysis.transaction_time:
            return PaymentDecision(
                "needs_review", True, True, "Transaction date and time are not visible"
            )
        try:
            transaction_date = datetime.fromisoformat(
                analysis.transaction_time.replace("Z", "+00:00")
            ).date()
        except ValueError:
            return PaymentDecision(
                "needs_review", True, True, "Transaction date and time could not be validated"
            )
        if transaction_date != expected_date:
            return PaymentDecision(
                "needs_review", True, True, "Transaction date does not match the menu date"
            )
    if Decimal(str(analysis.confidence)) < Decimal(str(minimum_confidence)):
        return PaymentDecision(
            "needs_review", True, True, "Receipt-reading confidence is too low"
        )
    return PaymentDecision(
        "needs_review", True, True, "Amount and recipient match; admin confirmation required"
    )
