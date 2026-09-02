import unittest
from datetime import date

from lunchbot.domain import ReceiptAnalysis, evaluate_payment


class PaymentDecisionTests(unittest.TestCase):
    def test_matching_receipt_is_ai_matched(self):
        analysis = ReceiptAnalysis(
            amount=35_000,
            recipient_name="Demo Recipient",
            transaction_time="2026-09-02 10:25",
            success_visible=True,
            confidence=0.94,
        )
        decision = evaluate_payment(
            analysis, 35_000, ("Demo Recipient",), expected_date=date(2026, 9, 2)
        )
        self.assertEqual(decision.status, "ai_matched")

    def test_wrong_amount_requires_review(self):
        analysis = ReceiptAnalysis(
            amount=30_000,
            recipient_name="Demo Recipient",
            transaction_time=None,
            success_visible=True,
            confidence=0.99,
        )
        decision = evaluate_payment(analysis, 35_000, ("Demo Recipient",))
        self.assertEqual(decision.status, "needs_review")
        self.assertFalse(decision.amount_matches)

    def test_low_confidence_requires_review(self):
        analysis = ReceiptAnalysis(
            amount=35_000,
            recipient_name="Demo Recipient",
            transaction_time=None,
            success_visible=True,
            confidence=0.50,
        )
        decision = evaluate_payment(analysis, 35_000, ("Demo Recipient",))
        self.assertEqual(decision.status, "needs_review")

    def test_old_receipt_requires_review(self):
        analysis = ReceiptAnalysis(
            amount=35_000,
            recipient_name="Demo Recipient",
            transaction_time="2026-09-01 10:25",
            success_visible=True,
            confidence=0.99,
        )
        decision = evaluate_payment(
            analysis, 35_000, ("Demo Recipient",), expected_date=date(2026, 9, 2)
        )
        self.assertEqual(decision.status, "needs_review")

    def test_missing_recipient_configuration_never_auto_matches(self):
        analysis = ReceiptAnalysis(
            amount=35_000,
            recipient_name="Demo Recipient",
            transaction_time="2026-09-02 10:25",
            success_visible=True,
            confidence=0.99,
        )
        decision = evaluate_payment(
            analysis, 35_000, (), expected_date=date(2026, 9, 2)
        )
        self.assertEqual(decision.status, "needs_review")


if __name__ == "__main__":
    unittest.main()
