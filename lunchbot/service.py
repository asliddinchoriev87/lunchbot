from __future__ import annotations

import hashlib
import logging
import sqlite3
import time as sleep_time
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from .ai_client import AIClient, AIError
from .config import Config
from .database import Database
from .domain import ReceiptAnalysis, evaluate_payment
from .formatting import (
    PAYMENT_LABELS,
    admin_menu_keyboard,
    caterer_text,
    menu_preview,
    mention,
    money,
    order_keyboard,
    ordering_text,
    payment_keyboard,
    registration_keyboard,
    summary_text,
)
from .parsing import looks_like_menu, parse_menu
from .scheduler import due_action
from .telegram_api import TelegramClient, TelegramError


LOGGER = logging.getLogger(__name__)


class LunchBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.database_path)
        self.telegram = TelegramClient(config.telegram_bot_token)
        self.ai = AIClient(config.openai_api_key, config.openai_model)
        self.timezone = ZoneInfo(config.timezone)

    def run(self) -> None:
        self.telegram.call("deleteWebhook", {"drop_pending_updates": False})
        me = self.telegram.call("getMe")
        LOGGER.info("LunchBot started as @%s", me.get("username", "unknown"))
        self.telegram.call(
            "setMyCommands",
            {
                "commands": [
                    {"command": "setup", "description": "Guruhni sozlash (admin)"},
                    {"command": "register", "description": "Eslatmalar uchun ro‘yxatdan o‘tish"},
                    {"command": "menu", "description": "Javob berilgan xabarni menyu qilish"},
                    {"command": "orders", "description": "Bugungi buyurtmalar"},
                    {"command": "close", "description": "Buyurtmani yopish (admin)"},
                    {"command": "help", "description": "Yordam"},
                ]
            },
        )

        offset: int | None = None
        while True:
            try:
                updates = self.telegram.get_updates(offset, timeout=10)
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    try:
                        self.handle_update(update)
                    except Exception:
                        LOGGER.exception("Update %s failed", update.get("update_id"))
                self.process_schedules()
            except TelegramError:
                LOGGER.exception("Telegram polling failed")
                sleep_time.sleep(3)
            except KeyboardInterrupt:
                LOGGER.info("Stopping LunchBot")
                break
        self.db.close()

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self.handle_callback(update["callback_query"])
        elif "message" in update:
            self.handle_message(update["message"])

    @staticmethod
    def _message_text(message: dict) -> str:
        return (message.get("text") or message.get("caption") or "").strip()

    @staticmethod
    def _person(user: dict) -> tuple[int, str, str | None]:
        return int(user["id"]), user.get("first_name") or "Telegram user", user.get("username")

    def _configured_group(self) -> int | None:
        value = self.db.get_setting("group_chat_id")
        return int(value) if value else None

    def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            return self.telegram.is_admin(chat_id, user_id)
        except TelegramError:
            LOGGER.exception("Could not check admin status")
            return False

    def handle_message(self, message: dict) -> None:
        chat = message["chat"]
        chat_id = int(chat["id"])
        user = message.get("from")
        if not user or user.get("is_bot"):
            return
        user_id, first_name, username = self._person(user)
        text = self._message_text(message)

        if text.startswith("/"):
            command = text.split()[0].split("@")[0].casefold()
            self.handle_command(command, message, user_id, first_name, username)
            return

        configured_group = self._configured_group()
        if configured_group != chat_id:
            return

        if message.get("photo") or (
            message.get("document", {}).get("mime_type", "").startswith("image/")
        ):
            self.handle_receipt(message, user_id, first_name, username)
            return

        if text and looks_like_menu(text):
            forwarded = bool(message.get("forward_origin"))
            if forwarded or self._is_group_admin(chat_id, user_id):
                self.db.upsert_user(user_id, first_name, username)
                self.create_menu_draft(
                    chat_id,
                    text,
                    reply_to=message["message_id"],
                    source_message_id=message["message_id"],
                )

    def handle_command(
        self,
        command: str,
        message: dict,
        user_id: int,
        first_name: str,
        username: str | None,
    ) -> None:
        chat = message["chat"]
        chat_id = int(chat["id"])
        is_group = chat.get("type") in {"group", "supergroup"}

        if command in {"/start", "/help"}:
            self.telegram.send_message(
                chat_id,
                "<b>LunchBot</b> kundalik tushlik buyurtmalarini yig‘adi.\n\n"
                "Admin: guruhda /setup yuboring.\n"
                "A’zolar: /register yuboring yoki ro‘yxatdan o‘tish tugmasini bosing.\n"
                "Menyu: oshxona xabarini guruhga forward qiling.\n"
                "Yopish: admin Close tugmasini bosadi.\n"
                "To‘lov: buyurtmadan keyin chek rasmini guruhga yuboring.",
            )
            return

        if not is_group:
            self.telegram.send_message(chat_id, "Bu buyruqni tushlik guruhida yuboring.")
            return

        if command == "/setup":
            if not self._is_group_admin(chat_id, user_id):
                self.telegram.send_message(chat_id, "Faqat guruh admini sozlashi mumkin.")
                return
            self.db.set_setting("group_chat_id", str(chat_id))
            self.db.upsert_user(user_id, first_name, username)
            self.telegram.send_message(
                chat_id,
                "<b>✅ LunchBot ushbu guruhga ulandi.</b>\n\n"
                "Har bir a’zo quyidagi tugmani bir marta bossin. Shunda bot faqat "
                "buyurtma bermaganlarga eslatma yubora oladi.",
                registration_keyboard(),
            )
            return

        if self._configured_group() != chat_id:
            self.telegram.send_message(chat_id, "Avval guruh admini /setup yuborishi kerak.")
            return

        if command == "/register":
            self.db.upsert_user(user_id, first_name, username)
            self.telegram.send_message(
                chat_id,
                f"✅ {mention(user_id, first_name)}, ro‘yxatdan o‘tdingiz.",
                reply_to_message_id=message["message_id"],
            )
        elif command == "/menu":
            if not self._is_group_admin(chat_id, user_id):
                self.telegram.send_message(chat_id, "Faqat admin menyuni qo‘lda ochishi mumkin.")
                return
            replied = message.get("reply_to_message") or {}
            source_text = self._message_text(replied)
            if not source_text:
                self.telegram.send_message(chat_id, "Menyu xabariga reply qilib /menu yuboring.")
                return
            self.db.upsert_user(user_id, first_name, username)
            self.create_menu_draft(
                chat_id,
                source_text,
                reply_to=message["message_id"],
                source_message_id=replied.get("message_id"),
            )
        elif command == "/orders":
            menu = self.db.latest_menu(chat_id, ("open", "closed"))
            if not menu:
                self.telegram.send_message(chat_id, "Hozircha menyu yo‘q.")
                return
            self.telegram.send_message(chat_id, summary_text(self.db.order_summary(menu["id"])))
        elif command == "/close":
            if not self._is_group_admin(chat_id, user_id):
                self.telegram.send_message(chat_id, "Faqat admin buyurtmani yopishi mumkin.")
                return
            menu = self.db.latest_menu(chat_id, ("open",))
            if not menu:
                self.telegram.send_message(chat_id, "Ochiq buyurtma yo‘q.")
                return
            self.close_order(menu["id"], chat_id)

    def create_menu_draft(
        self,
        chat_id: int,
        text: str,
        reply_to: int | None = None,
        source_message_id: int | None = None,
    ) -> None:
        try:
            try:
                parsed = parse_menu(text, datetime.now(self.timezone).date())
            except ValueError:
                if not self.ai.enabled:
                    raise
                parsed = self.ai.extract_menu(text, datetime.now(self.timezone).date())
            menu_id = self.db.create_menu(chat_id, parsed, source_message_id)
            if menu_id is None:
                LOGGER.info(
                    "Ignoring duplicate menu message %s in chat %s",
                    source_message_id,
                    chat_id,
                )
                return
            self.telegram.send_message(
                chat_id,
                menu_preview(parsed),
                admin_menu_keyboard(menu_id),
                reply_to_message_id=reply_to,
            )
        except (ValueError, AIError) as exc:
            self.telegram.send_message(
                chat_id,
                "❌ Menyuni tushunmadim. Admin xabarni tekshirib, menyuga reply qilib /menu yuborsin.\n"
                f"Sabab: {escape(str(exc))}",
                reply_to_message_id=reply_to,
            )

    def handle_callback(self, callback: dict) -> None:
        callback_id = callback["id"]
        data = callback.get("data", "")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        user_id, first_name, username = self._person(callback["from"])

        if data == "register":
            self.db.upsert_user(user_id, first_name, username)
            self.telegram.answer_callback(callback_id, "Ro‘yxatdan o‘tdingiz ✅")
            return

        if data.startswith(
            ("menu_confirm:", "menu_cancel:", "menu_close:", "payment_verify:", "payment_reject:")
        ):
            if not self._is_group_admin(chat_id, user_id):
                self.telegram.answer_callback(callback_id, "Faqat admin uchun", alert=True)
                return

        if data.startswith("menu_confirm:"):
            menu_id = int(data.rsplit(":", 1)[1])
            menu = self.db.get_menu(menu_id)
            if not menu or not self.db.confirm_menu(menu_id):
                self.telegram.answer_callback(callback_id, "Menyu allaqachon qayta ishlangan", alert=True)
                return
            items = self.db.get_menu_items(menu_id)
            sent = self.telegram.send_message(
                chat_id, ordering_text(menu, items), order_keyboard(menu_id, items)
            )
            self.db.set_order_message_id(menu_id, sent["message_id"])
            self.telegram.edit_message(
                chat_id,
                message["message_id"],
                escape(message.get("text", "Menyu")) + "\n\n<b>✅ Tasdiqlandi</b>",
            )
            self.telegram.answer_callback(callback_id, "Buyurtma ochildi ✅")
            return

        if data.startswith("menu_cancel:"):
            menu_id = int(data.rsplit(":", 1)[1])
            self.db.cancel_menu(menu_id)
            self.telegram.edit_message(
                chat_id,
                message["message_id"],
                escape(message.get("text", "Menyu")) + "\n\n<b>❌ Bekor qilindi</b>",
            )
            self.telegram.answer_callback(callback_id, "Bekor qilindi")
            return

        if data.startswith("menu_close:"):
            menu_id = int(data.rsplit(":", 1)[1])
            menu = self.db.get_menu(menu_id)
            if not menu or menu["status"] != "open":
                self.telegram.answer_callback(callback_id, "Buyurtma allaqachon yopilgan", alert=True)
                return
            self.close_order(menu_id, chat_id)
            self.telegram.answer_callback(callback_id, "Buyurtma yopildi 🔒")
            return

        if data.startswith("order:"):
            _, menu_id_raw, item_id_raw = data.split(":", 2)
            menu_id, item_id = int(menu_id_raw), int(item_id_raw)
            menu = self.db.get_menu(menu_id)
            if not menu or menu["status"] != "open":
                self.telegram.answer_callback(callback_id, "Buyurtma yopilgan", alert=True)
                return
            self.db.upsert_user(user_id, first_name, username)
            self.db.set_order(menu_id, user_id, item_id)
            item = next(row for row in self.db.get_menu_items(menu_id) if row["id"] == item_id)
            self.telegram.answer_callback(callback_id, f"Tanlandi: {item['name']} ✅")
            return

        if data.startswith("payment_verify:") or data.startswith("payment_reject:"):
            payment_id = int(data.rsplit(":", 1)[1])
            status = "verified" if data.startswith("payment_verify:") else "rejected"
            payment = self.db.get_payment(payment_id)
            if not payment:
                self.telegram.answer_callback(callback_id, "To‘lov topilmadi", alert=True)
                return
            self.db.update_payment_status(payment_id, status)
            self.telegram.edit_message(
                chat_id,
                message["message_id"],
                escape(message.get("text", "To‘lov")) + f"\n\n<b>{PAYMENT_LABELS[status]}</b>",
            )
            self.telegram.answer_callback(callback_id, PAYMENT_LABELS[status])

    def handle_receipt(
        self, message: dict, user_id: int, first_name: str, username: str | None
    ) -> None:
        chat_id = int(message["chat"]["id"])
        self.db.upsert_user(user_id, first_name, username)
        order = self.db.latest_order_for_user(chat_id, user_id)
        if not order:
            self.telegram.send_message(
                chat_id,
                "Avval taom tanlang, keyin to‘lov chekini yuboring.",
                reply_to_message_id=message["message_id"],
            )
            return

        if message.get("photo"):
            file_info = message["photo"][-1]
            mime_type = "image/jpeg"
        else:
            file_info = message["document"]
            mime_type = file_info.get("mime_type", "image/jpeg")
        image_bytes = self.telegram.download_file(file_info["file_id"])
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        if self.ai.enabled:
            try:
                analysis = self.ai.analyze_receipt(image_bytes, mime_type)
            except AIError as exc:
                LOGGER.exception("Receipt analysis failed")
                analysis = ReceiptAnalysis(None, None, None, False, 0, str(exc))
        else:
            analysis = ReceiptAnalysis(
                None, None, None, False, 0, "OPENAI_API_KEY is not configured"
            )
        decision = evaluate_payment(
            analysis,
            int(order["price"]),
            self.config.payment_recipients,
            expected_date=date.fromisoformat(order["menu_date"]),
        )
        note = "; ".join(filter(None, (decision.reason, analysis.note)))
        try:
            payment_id = self.db.add_payment(
                order_id=order["order_id"],
                file_unique_id=file_info.get("file_unique_id"),
                image_hash=image_hash,
                expected_amount=int(order["price"]),
                analysis=analysis,
                status=decision.status,
                note=note,
            )
        except sqlite3.IntegrityError:
            self.telegram.send_message(
                chat_id,
                "⛔ Bu chek avval yuborilgan.",
                reply_to_message_id=message["message_id"],
            )
            return

        extracted = money(analysis.amount) if analysis.amount is not None else "o‘qilmadi"
        recipient = escape(analysis.recipient_name or "o‘qilmadi")
        confidence = f"{round(analysis.confidence * 100)}%"
        self.telegram.send_message(
            chat_id,
            f"<b>🧾 {escape(first_name)} — {escape(order['meal_name'])}</b>\n"
            f"Kutilgan summa: <b>{money(order['price'])}</b>\n"
            f"Chekdagi summa: <b>{extracted}</b>\n"
            f"Qabul qiluvchi: {recipient}\n"
            f"AI ishonchi: {confidence}\n"
            f"Holat: <b>{PAYMENT_LABELS[decision.status]}</b>",
            payment_keyboard(payment_id),
            reply_to_message_id=message["message_id"],
        )

    def process_schedules(self) -> None:
        now = datetime.now(self.timezone)
        for menu in self.db.open_menus():
            action = due_action(
                now,
                date.fromisoformat(menu["menu_date"]),
                menu["status"],
                self.config.reminder_times,
                self.db.sent_reminders(menu["id"]),
            )
            if not action:
                continue
            _, labels = action
            self.send_reminders(menu["id"], menu["chat_id"], labels[-1])
            self.db.mark_reminders(menu["id"], labels)

    def send_reminders(self, menu_id: int, chat_id: int, label: str) -> None:
        missing = self.db.users_without_order(menu_id)
        if not missing:
            return
        for start in range(0, len(missing), 25):
            chunk = missing[start : start + 25]
            names = " ".join(mention(row["user_id"], row["first_name"]) for row in chunk)
            urgent = " Oxirgi eslatma!" if label == self.config.reminder_times[-1] else ""
            self.telegram.send_message(
                chat_id,
                f"<b>⏰ {label} — tushlikni tanlang.{urgent}</b>\n{names}\n"
                "Buyurtma admin yopmaguncha ochiq.",
            )

    def close_order(self, menu_id: int, chat_id: int) -> None:
        menu = self.db.get_menu(menu_id)
        if not menu or menu["status"] != "open":
            return
        self.db.close_menu(menu_id)
        if menu["order_message_id"]:
            try:
                items = self.db.get_menu_items(menu_id)
                self.telegram.edit_message(
                    chat_id,
                    menu["order_message_id"],
                    ordering_text(menu, items) + "\n\n<b>🔒 Buyurtma yopildi.</b>",
                    {"inline_keyboard": []},
                )
            except TelegramError:
                LOGGER.exception("Could not remove the closed order buttons")
        summary = self.db.order_summary(menu_id)
        self.telegram.send_message(chat_id, caterer_text(summary))
        self.telegram.send_message(chat_id, summary_text(summary, "🔒 Buyurtma yopildi"))
