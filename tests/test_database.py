import sqlite3
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
        menu_id = self.db.create_menu(
            -1001, self.menu, source_chat_id=7, source_message_id=55
        )
        self.assertEqual(self.db.get_menu(menu_id)["source_message_id"], 55)
        self.assertEqual(self.db.get_menu(menu_id)["source_chat_id"], 7)
        self.assertIsNone(
            self.db.create_menu(
                -1001, self.menu, source_chat_id=7, source_message_id=55
            )
        )
        self.assertIsNone(
            self.db.create_menu(
                -1001, self.menu, source_chat_id=7, source_message_id=56
            )
        )
        self.assertTrue(self.db.confirm_menu(menu_id))
        items = self.db.get_menu_items(menu_id)
        self.db.upsert_user(1, "Asliddin", "asliddin")
        self.db.upsert_user(2, "Dilorom", None, private_chat_id=2)

        self.db.set_order(menu_id, 1, items[0]["id"])
        self.assertEqual([row["user_id"] for row in self.db.users_without_order(menu_id)], [2])
        self.assertEqual(self.db.users_without_order(menu_id)[0]["private_chat_id"], 2)

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

    def test_existing_database_gets_private_flow_columns(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
                username TEXT, active INTEGER NOT NULL DEFAULT 1,
                registered_at TEXT NOT NULL
            );
            CREATE TABLE menus (
                id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                menu_date TEXT NOT NULL, raw_text TEXT NOT NULL,
                portion_price INTEGER NOT NULL, delivery_fee INTEGER NOT NULL,
                free_delivery_min INTEGER, status TEXT NOT NULL,
                order_message_id INTEGER, source_message_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
                file_unique_id TEXT, image_hash TEXT NOT NULL UNIQUE,
                expected_amount INTEGER NOT NULL, extracted_amount INTEGER,
                recipient_name TEXT, transaction_time TEXT, confidence REAL NOT NULL,
                success_visible INTEGER NOT NULL, status TEXT NOT NULL,
                note TEXT, created_at TEXT NOT NULL
            );
            """
        )
        connection.close()

        migrated = Database(str(legacy_path))
        user_columns = {
            row["name"] for row in migrated.connection.execute("PRAGMA table_info(users)")
        }
        payment_columns = {
            row["name"] for row in migrated.connection.execute("PRAGMA table_info(payments)")
        }
        menu_columns = {
            row["name"] for row in migrated.connection.execute("PRAGMA table_info(menus)")
        }
        migrated.close()

        self.assertIn("private_chat_id", user_columns)
        self.assertIn("telegram_file_id", payment_columns)
        self.assertIn("source_chat_id", menu_columns)
        self.assertIn("dedupe_key", menu_columns)


if __name__ == "__main__":
    unittest.main()
