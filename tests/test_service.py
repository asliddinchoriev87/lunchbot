import tempfile
import unittest
from pathlib import Path

from lunchbot.config import Config
from lunchbot.service import LunchBot

from test_parsing import SAMPLE_MENU


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.answers = []
        self.photos = []
        self.admin_ids = {1}

    def send_message(self, chat_id, text, reply_markup=None, reply_to_message_id=None):
        result = {"message_id": len(self.sent) + 100}
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "reply_to_message_id": reply_to_message_id,
                **result,
            }
        )
        return result

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.edited.append((chat_id, message_id, text, reply_markup))
        return {"message_id": message_id}

    def edit_caption(self, chat_id, message_id, caption, reply_markup=None):
        self.edited.append((chat_id, message_id, caption, reply_markup))
        return {"message_id": message_id}

    def send_photo(self, chat_id, file_id, caption, reply_markup=None):
        result = {"message_id": len(self.sent) + len(self.photos) + 100}
        self.photos.append((chat_id, file_id, caption, reply_markup))
        return result

    def answer_callback(self, callback_query_id, text="", alert=False):
        self.answers.append((callback_query_id, text, alert))

    def is_admin(self, chat_id, user_id):
        return user_id in self.admin_ids

    def download_file(self, file_id):
        return b"fake receipt image"


class ServiceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config = Config(
            telegram_bot_token="test-token",
            database_path=str(Path(self.temp_dir.name) / "service.db"),
            timezone="Asia/Tashkent",
            reminder_times=("10:20", "10:35", "10:50"),
            openai_api_key=None,
            openai_model="gpt-5-mini",
            payment_recipients=("Recipient",),
            log_level="INFO",
        )
        self.bot = LunchBot(config)
        self.telegram = FakeTelegram()
        self.bot.telegram = self.telegram
        self.bot.bot_username = "test_lunchbot"
        self.bot.db.set_setting("group_chat_id", "-1001")

    def tearDown(self):
        self.bot.db.close()
        self.temp_dir.cleanup()

    def test_menu_can_be_drafted_and_confirmed(self):
        self.bot.create_menu_draft(-1001, SAMPLE_MENU, reply_to=10)
        draft = self.bot.db.latest_menu(-1001, ("draft",))
        self.assertIsNotNone(draft)
        self.assertIn("Menyu topildi", self.telegram.sent[-1]["text"])

        callback = {
            "id": "callback-1",
            "data": f"menu_confirm:{draft['id']}",
            "from": {"id": 1, "first_name": "Admin", "username": "admin"},
            "message": {
                "message_id": 100,
                "text": "Menu preview",
                "chat": {"id": -1001, "type": "supergroup"},
            },
        }
        self.bot.handle_callback(callback)
        self.assertEqual(self.bot.db.get_menu(draft["id"])["status"], "open")
        self.assertTrue(self.telegram.answers)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertIn("Bugungi tushlik", self.telegram.edited[-1][2])
        dashboard_keyboard = self.telegram.edited[-1][3]
        close_button = dashboard_keyboard["inline_keyboard"][-1][-1]
        self.assertEqual(close_button["callback_data"], f"menu_close:{draft['id']}")

    def test_admin_submits_menu_privately_and_only_dashboard_goes_to_group(self):
        self.bot.handle_message(
            {
                "message_id": 50,
                "text": SAMPLE_MENU,
                "forward_origin": {"type": "channel"},
                "from": {"id": 1, "first_name": "Admin", "username": "admin"},
                "chat": {"id": 1, "type": "private"},
            }
        )
        draft = self.bot.db.latest_menu(-1001, ("draft",))
        self.assertEqual(self.telegram.sent[-1]["chat_id"], 1)

        self.bot.handle_callback(
            {
                "id": "private-confirm",
                "data": f"menu_confirm:{draft['id']}",
                "from": {"id": 1, "first_name": "Admin", "username": "admin"},
                "message": {
                    "message_id": self.telegram.sent[-1]["message_id"],
                    "text": "Menu preview",
                    "chat": {"id": 1, "type": "private"},
                },
            }
        )

        group_messages = [row for row in self.telegram.sent if row["chat_id"] == -1001]
        self.assertEqual(len(group_messages), 1)
        self.assertIn("Buyurtmalar va to‘lovlar", group_messages[0]["text"])

    def test_receipt_without_ai_is_saved_for_review(self):
        self.bot.create_menu_draft(-1001, SAMPLE_MENU)
        menu = self.bot.db.latest_menu(-1001, ("draft",))
        self.bot.db.confirm_menu(menu["id"])
        item = self.bot.db.get_menu_items(menu["id"])[0]
        self.bot.db.upsert_user(7, "Asliddin", "asliddin")
        self.bot.db.set_order(menu["id"], 7, item["id"])

        self.bot.handle_receipt(
            {
                "message_id": 55,
                "chat": {"id": 7, "type": "private"},
                "photo": [
                    {"file_id": "file-1", "file_unique_id": "unique-1"}
                ],
            },
            user_id=7,
            first_name="Asliddin",
            username="asliddin",
            group_chat_id=-1001,
        )

        summary = self.bot.db.order_summary(menu["id"])
        self.assertEqual(summary.rows[0]["payment_status"], "needs_review")
        self.assertIn("Tekshirish kerak", self.telegram.sent[-1]["text"])
        self.bot.show_admin_panel(1, 1, menu["id"])
        self.assertEqual(self.telegram.photos[-1][0:2], (1, "file-1"))

    def test_user_orders_in_private_chat_and_group_dashboard_updates(self):
        self.bot.create_menu_draft(-1001, SAMPLE_MENU)
        menu = self.bot.db.latest_menu(-1001, ("draft",))
        self.bot.db.confirm_menu(menu["id"])
        self.bot.db.set_order_message_id(menu["id"], 100)

        self.bot.handle_command(
            "/start",
            {"message_id": 5, "chat": {"id": 7, "type": "private"}},
            7,
            "User",
            "user",
            f"order_{menu['id']}",
        )
        private_message = self.telegram.sent[-1]
        item = self.bot.db.get_menu_items(menu["id"])[0]
        self.bot.handle_callback(
            {
                "id": "private-order",
                "data": f"order:{menu['id']}:{item['id']}",
                "from": {"id": 7, "first_name": "User", "username": "user"},
                "message": {
                    "message_id": private_message["message_id"],
                    "chat": {"id": 7, "type": "private"},
                },
            }
        )

        order = self.bot.db.order_for_user(menu["id"], 7)
        self.assertEqual(order["meal_name"], item["name"])
        self.assertEqual(
            self.bot.db.users_without_order(menu["id"]), []
        )
        self.assertTrue(any(edit[0] == -1001 for edit in self.telegram.edited))
        self.assertTrue(any(edit[0] == 7 for edit in self.telegram.edited))

    def test_reminder_is_sent_only_to_private_chat(self):
        self.bot.create_menu_draft(-1001, SAMPLE_MENU)
        menu = self.bot.db.latest_menu(-1001, ("draft",))
        self.bot.db.confirm_menu(menu["id"])
        self.bot.db.upsert_user(7, "User", "user", private_chat_id=7)

        self.bot.send_reminders(menu["id"], "10:20")

        self.assertEqual(self.telegram.sent[-1]["chat_id"], 7)
        self.assertNotEqual(self.telegram.sent[-1]["chat_id"], -1001)

    def test_only_admin_can_close_order(self):
        self.bot.create_menu_draft(-1001, SAMPLE_MENU)
        menu = self.bot.db.latest_menu(-1001, ("draft",))
        self.bot.db.confirm_menu(menu["id"])

        callback = {
            "id": "close-by-other",
            "data": f"menu_close:{menu['id']}",
            "from": {"id": 8, "first_name": "Other"},
            "message": {"message_id": 101, "chat": {"id": -1001, "type": "supergroup"}},
        }
        self.bot.handle_callback(callback)
        self.assertEqual(self.bot.db.get_menu(menu["id"])["status"], "open")
        self.assertTrue(self.telegram.answers[-1][2])

        callback["id"] = "close-by-admin"
        callback["from"] = {"id": 1, "first_name": "Admin"}
        self.bot.handle_callback(callback)
        self.assertEqual(self.bot.db.get_menu(menu["id"])["status"], "closed")
        self.assertIn("Buyurtma yopildi", self.telegram.answers[-1][1])

    def test_duplicate_menu_message_is_ignored(self):
        self.bot.create_menu_draft(-1001, SAMPLE_MENU, source_message_id=55)
        self.bot.create_menu_draft(-1001, SAMPLE_MENU, source_message_id=55)
        self.assertEqual(len(self.telegram.sent), 1)


if __name__ == "__main__":
    unittest.main()
