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

    def answer_callback(self, callback_query_id, text="", alert=False):
        self.answers.append((callback_query_id, text, alert))

    def is_admin(self, chat_id, user_id):
        return True

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
            order_open_time="10:00",
            order_close_time="11:00",
            openai_api_key=None,
            openai_model="gpt-5-mini",
            payment_recipients=("Recipient",),
            log_level="INFO",
        )
        self.bot = LunchBot(config)
        self.telegram = FakeTelegram()
        self.bot.telegram = self.telegram
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
        self.assertIn("Bugungi tushlik", self.telegram.sent[-1]["text"])

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
                "chat": {"id": -1001, "type": "supergroup"},
                "photo": [
                    {"file_id": "file-1", "file_unique_id": "unique-1"}
                ],
            },
            user_id=7,
            first_name="Asliddin",
            username="asliddin",
        )

        summary = self.bot.db.order_summary(menu["id"])
        self.assertEqual(summary.rows[0]["payment_status"], "needs_review")
        self.assertIn("Tekshirish kerak", self.telegram.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
