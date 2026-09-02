import tempfile
import unittest
from datetime import date
from pathlib import Path

from lunchbot.database import Database
from lunchbot.domain import ParsedMenu, ReceiptAnalysis


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "test.db"))
        self.menu = ParsedMenu(
            menu_date=date(2026, 9, 2),
            items=("Osh", "Manti"),
            portion_price=35_000,
            delivery_fee=20_000,
            free_delivery_min=5,
            raw_text="test",
        )

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_registration_order_update_and_summary(self):
        menu_id = self.db.create_menu(-1001, self.menu, source_message_id=55)
        self.assertEqual(self.db.get_menu(menu_id)["source_message_id"], 55)
        self.assertIsNone(self.db.create_menu(-1001, self.menu, source_message_id=55))
        self.assertTrue(self.db.confirm_menu(menu_id))
        items = self.db.get_menu_items(menu_id)
        self.db.upsert_user(1, "Asliddin", "asliddin")
        self.db.upsert_user(2, "Dilorom", None)

        self.db.set_order(menu_id, 1, items[0]["id"])
        self.assertEqual([row["user_id"] for row in self.db.users_without_order(menu_id)], [2])

        self.db.set_order(menu_id, 1, items[1]["id"])
        summary = self.db.order_summary(menu_id)
        self.assertEqual(summary.portion_count, 1)
        self.assertEqual(summary.rows[0]["meal_name"], "Manti")
        self.assertEqual(summary.grand_total, 55_000)

        analysis = ReceiptAnalysis(35_000, "Recipient", None, True, 0.95)
        payment_id = self.db.add_payment(
            summary.rows[0]["order_id"], "unique", "hash", 35_000, analysis, "ai_matched", "ok"
        )
        self.db.update_payment_status(payment_id, "verified")
        self.assertEqual(self.db.order_summary(menu_id).rows[0]["payment_status"], "verified")


if __name__ == "__main__":
    unittest.main()
