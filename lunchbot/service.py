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
    group_dashboard_keyboard,
    group_dashboard_text,
    menu_preview,
    mention,
    money,
    payment_keyboard,
    private_order_keyboard,
    private_order_text,
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
        self.bot_username = ""

    def run(self) -> None:
        self.telegram.call("deleteWebhook", {"drop_pending_updates": False})
        me = self.telegram.call("getMe")
        self.bot_username = me.get("username", "")
        LOGGER.info("LunchBot started as @%s", self.bot_username or "unknown")
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

        saved_offset = self.db.get_setting("telegram_update_offset")
        offset = int(saved_offset) if saved_offset else None
        while True:
            try:
                updates = self.telegram.get_updates(offset, timeout=10)
                for update in updates:
                    next_offset = int(update["update_id"]) + 1
                    try:
                        self.handle_update(update)
                    except Exception:
                        LOGGER.exception("Update %s failed", update.get("update_id"))
                    finally:
                        offset = next_offset
                        self.db.set_setting("telegram_update_offset", str(offset))
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
            parts = text.split(maxsplit=1)
            command = parts[0].split("@")[0].casefold()
            argument = parts[1].strip() if len(parts) > 1 else ""
            self.handle_command(
                command, message, user_id, first_name, username, argument
            )
            return

        configured_group = self._configured_group()
        is_image = message.get("photo") or (
            message.get("document", {}).get("mime_type", "").startswith("image/")
        )
        if is_image:
            if chat.get("type") == "private" and configured_group is not None:
                self.handle_receipt(
                    message, user_id, first_name, username, configured_group
                )
            elif configured_group == chat_id:
                self.telegram.send_message(
                    chat_id,
                    "Chekni guruhga emas, botning shaxsiy chatiga yuboring.",
                    registration_keyboard(self.bot_username),
                    reply_to_message_id=message["message_id"],
                )
            return

        if chat.get("type") == "private" and text and looks_like_menu(text):
            if configured_group is None:
                self.telegram.send_message(
                    chat_id, "Avval admin guruhda /setup yuborishi kerak."
                )
                return
            if not self._is_group_admin(configured_group, user_id):
                self.telegram.send_message(
                    chat_id, "Menyuni faqat guruh admini yuborishi mumkin."
                )
                return
            self.create_menu_draft(
                configured_group,
                text,
                preview_chat_id=chat_id,
                reply_to=message["message_id"],
                source_chat_id=chat_id,
                source_message_id=message["message_id"],
            )
            return

        if configured_group != chat_id:
            return

        if text and looks_like_menu(text):
            forwarded = bool(message.get("forward_origin"))
            if forwarded or self._is_group_admin(chat_id, user_id):
                self.db.upsert_user(user_id, first_name, username)
                self.create_menu_draft(
                    chat_id,
                    text,
                    preview_chat_id=chat_id,
                    reply_to=message["message_id"],
                    source_chat_id=chat_id,
                    source_message_id=message["message_id"],
                )

    def handle_command(
        self,
        command: str,
        message: dict,
        user_id: int,
        first_name: str,
        username: str | None,
        argument: str = "",
    ) -> None:
        chat = message["chat"]
        chat_id = int(chat["id"])
        is_group = chat.get("type") in {"group", "supergroup"}
        is_private = chat.get("type") == "private"

        if is_private:
            self.db.upsert_user(
                user_id, first_name, username, private_chat_id=chat_id
            )
            if command == "/start" and argument.startswith("order_"):
                try:
                    menu_id = int(argument.removeprefix("order_"))
                except ValueError:
                    self.telegram.send_message(chat_id, "Menyu havolasi noto‘g‘ri.")
                    return
                self.show_private_order(chat_id, user_id, menu_id)
                return
            if command == "/start" and argument.startswith("admin_"):
                try:
                    menu_id = int(argument.removeprefix("admin_"))
                except ValueError:
                    self.telegram.send_message(chat_id, "Admin havolasi noto‘g‘ri.")
                    return
                self.show_admin_panel(chat_id, user_id, menu_id)
                return
            if command == "/orders":
                self.show_personal_status(chat_id, user_id)
                return
            if command in {"/start", "/help", "/register"}:
                menu = None
                group_id = self._configured_group()
                if group_id is not None:
                    menu = self.db.latest_menu(group_id, ("open",))
                if menu:
                    self.show_private_order(chat_id, user_id, menu["id"])
                else:
                    self.telegram.send_message(
                        chat_id,
                        "✅ Shaxsiy chat ulandi. Yangi menyu ochilganda guruhdagi "
                        "Taom tanlash tugmasini bosing.",
                    )
                return

        if command in {"/start", "/help"}:
            self.telegram.send_message(
                chat_id,
                "<b>LunchBot</b> kundalik tushlik buyurtmalarini yig‘adi.\n\n"
                "Admin: guruhda /setup yuboring.\n"
                "A’zolar: shaxsiy chat tugmasini bir marta bosing.\n"
                "Menyu: admin oshxona xabarini botga shaxsiy forward qiladi.\n"
                "Yopish: admin Close tugmasini bosadi.\n"
                "To‘lov: buyurtmadan keyin chek rasmini botga shaxsiy yuboring.",
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
                registration_keyboard(self.bot_username),
            )
            return

        if self._configured_group() != chat_id:
            self.telegram.send_message(chat_id, "Avval guruh admini /setup yuborishi kerak.")
            return

        if command == "/register":
            self.telegram.send_message(
                chat_id,
                f"{mention(user_id, first_name)}, ro‘yxatdan o‘tish uchun botning shaxsiy chatini oching.",
                registration_keyboard(self.bot_username),
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
                preview_chat_id=chat_id,
                reply_to=message["message_id"],
                source_chat_id=chat_id,
                source_message_id=replied.get("message_id"),
            )
        elif command == "/orders":
            menu = self.db.latest_menu(chat_id, ("open", "closed"))
            if not menu:
                return
            self.refresh_group_dashboard(menu["id"])
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
        preview_chat_id: int | None = None,
        reply_to: int | None = None,
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
    ) -> None:
        try:
            try:
                parsed = parse_menu(text, datetime.now(self.timezone).date())
            except ValueError:
                if not self.ai.enabled:
                    raise
                parsed = self.ai.extract_menu(text, datetime.now(self.timezone).date())
            menu_id = self.db.create_menu(
                chat_id,
                parsed,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
            if menu_id is None:
                LOGGER.info(
                    "Ignoring duplicate menu message %s in chat %s",
                    source_message_id,
                    chat_id,
                )
                return
            self.telegram.send_message(
                preview_chat_id or chat_id,
                menu_preview(parsed),
                admin_menu_keyboard(menu_id),
                reply_to_message_id=reply_to,
            )
        except (ValueError, AIError) as exc:
            self.telegram.send_message(
                preview_chat_id or chat_id,
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
            private_chat_id = chat_id if chat.get("type") == "private" else None
            self.db.upsert_user(
                user_id, first_name, username, private_chat_id=private_chat_id
            )
            self.telegram.answer_callback(callback_id, "Ro‘yxatdan o‘tdingiz ✅")
            return

        if data.startswith(
            ("menu_confirm:", "menu_cancel:", "menu_close:")
        ):
            menu_id = int(data.rsplit(":", 1)[1])
            menu = self.db.get_menu(menu_id)
            group_chat_id = int(menu["chat_id"]) if menu else chat_id
            if not self._is_group_admin(group_chat_id, user_id):
                self.telegram.answer_callback(callback_id, "Faqat admin uchun", alert=True)
                return

        if data.startswith("menu_confirm:"):
            menu_id = int(data.rsplit(":", 1)[1])
            menu = self.db.get_menu(menu_id)
            if not menu or not self.db.confirm_menu(menu_id):
                self.telegram.answer_callback(callback_id, "Menyu allaqachon qayta ishlangan", alert=True)
                return
            if chat.get("type") in {"group", "supergroup"}:
                self.db.set_order_message_id(menu_id, message["message_id"])
                self.refresh_group_dashboard(menu_id)
            else:
                self.refresh_group_dashboard(menu_id)
                self.telegram.edit_message(
                    chat_id,
                    message["message_id"],
                    escape(message.get("text", "Menyu"))
                    + "\n\n<b>✅ Guruhda e’lon qilindi</b>",
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
            is_private = chat.get("type") == "private"
            self.db.upsert_user(
                user_id,
                first_name,
                username,
                private_chat_id=chat_id if is_private else None,
            )
            self.db.set_order(menu_id, user_id, item_id)
            item = next(row for row in self.db.get_menu_items(menu_id) if row["id"] == item_id)
            self.refresh_group_dashboard(menu_id)
            if is_private:
                order = self.db.order_for_user(menu_id, user_id)
                self.telegram.edit_message(
                    chat_id,
                    message["message_id"],
                    private_order_text(menu, self.db.get_menu_items(menu_id), order),
                    private_order_keyboard(menu_id, self.db.get_menu_items(menu_id)),
                )
            self.telegram.answer_callback(callback_id, f"Tanlandi: {item['name']} ✅")
            return

        if data.startswith("payment_verify:") or data.startswith("payment_reject:"):
            payment_id = int(data.rsplit(":", 1)[1])
            status = "verified" if data.startswith("payment_verify:") else "rejected"
            payment = self.db.get_payment(payment_id)
            if not payment:
                self.telegram.answer_callback(callback_id, "To‘lov topilmadi", alert=True)
                return
            if not self._is_group_admin(payment["chat_id"], user_id):
                self.telegram.answer_callback(callback_id, "Faqat admin uchun", alert=True)
                return
            self.db.update_payment_status(payment_id, status)
            updated_text = escape(
                message.get("caption") or message.get("text") or "To‘lov"
            ) + f"\n\n<b>{PAYMENT_LABELS[status]}</b>"
            if message.get("photo"):
                self.telegram.edit_caption(
                    chat_id, message["message_id"], updated_text
                )
            else:
                self.telegram.edit_message(
                    chat_id, message["message_id"], updated_text
                )
            self.telegram.answer_callback(callback_id, PAYMENT_LABELS[status])
            self.refresh_group_dashboard(payment["menu_id"])

    def handle_receipt(
        self,
        message: dict,
        user_id: int,
        first_name: str,
        username: str | None,
        group_chat_id: int,
    ) -> None:
        chat_id = int(message["chat"]["id"])
        self.db.upsert_user(
            user_id, first_name, username, private_chat_id=chat_id
        )
        order = self.db.latest_order_for_user(group_chat_id, user_id)
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
            self.db.add_payment(
                order_id=order["order_id"],
                file_unique_id=file_info.get("file_unique_id"),
                image_hash=image_hash,
                expected_amount=int(order["price"]),
                analysis=analysis,
                status=decision.status,
                note=note,
                telegram_file_id=file_info.get("file_id"),
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
        self.refresh_group_dashboard(order["menu_id"])
        self.telegram.send_message(
            chat_id,
            f"<b>🧾 {escape(first_name)} — {escape(order['meal_name'])}</b>\n"
            f"Kutilgan summa: <b>{money(order['price'])}</b>\n"
            f"Chekdagi summa: <b>{extracted}</b>\n"
            f"Qabul qiluvchi: {recipient}\n"
            f"AI ishonchi: {confidence}\n"
            f"Holat: <b>{PAYMENT_LABELS[decision.status]}</b>",
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
            self.send_reminders(menu["id"], labels[-1])
            self.db.mark_reminders(menu["id"], labels)

    def send_reminders(self, menu_id: int, label: str) -> None:
        missing = self.db.users_without_order(menu_id)
        if not missing:
            return
        menu = self.db.get_menu(menu_id)
        items = self.db.get_menu_items(menu_id)
        urgent = " Oxirgi eslatma!" if label == self.config.reminder_times[-1] else ""
        for row in missing:
            private_chat_id = row["private_chat_id"]
            if private_chat_id is None:
                continue
            try:
                self.telegram.send_message(
                    private_chat_id,
                    f"<b>⏰ {label} — tushlikni tanlang.{urgent}</b>\n\n"
                    + private_order_text(menu, items),
                    private_order_keyboard(menu_id, items),
                )
            except TelegramError:
                LOGGER.exception("Could not send a private reminder to %s", row["user_id"])

    def close_order(self, menu_id: int, chat_id: int) -> None:
        menu = self.db.get_menu(menu_id)
        if not menu or menu["status"] != "open":
            return
        self.db.close_menu(menu_id)
        self.refresh_group_dashboard(menu_id)

    def refresh_group_dashboard(self, menu_id: int) -> None:
        menu = self.db.get_menu(menu_id)
        if not menu:
            return
        items = self.db.get_menu_items(menu_id)
        summary = self.db.order_summary(menu_id)
        text = group_dashboard_text(menu, items, summary)
        keyboard = group_dashboard_keyboard(
            menu_id, self.bot_username, menu["status"] == "open"
        )
        try:
            if menu["order_message_id"]:
                self.telegram.edit_message(
                    menu["chat_id"], menu["order_message_id"], text, keyboard
                )
            else:
                sent = self.telegram.send_message(menu["chat_id"], text, keyboard)
                self.db.set_order_message_id(menu_id, sent["message_id"])
        except TelegramError as exc:
            error = str(exc).casefold()
            if "message is not modified" in error:
                return
            if "message to edit not found" in error and menu["order_message_id"]:
                try:
                    sent = self.telegram.send_message(menu["chat_id"], text, keyboard)
                    self.db.set_order_message_id(menu_id, sent["message_id"])
                    return
                except TelegramError:
                    LOGGER.exception(
                        "Could not recreate group dashboard for menu %s", menu_id
                    )
                    return
            LOGGER.exception("Could not refresh group dashboard for menu %s", menu_id)

    def show_private_order(self, chat_id: int, user_id: int, menu_id: int) -> None:
        menu = self.db.get_menu(menu_id)
        configured_group = self._configured_group()
        if not menu or menu["chat_id"] != configured_group:
            self.telegram.send_message(chat_id, "Menyu topilmadi.")
            return
        items = self.db.get_menu_items(menu_id)
        order = self.db.order_for_user(menu_id, user_id)
        keyboard = (
            private_order_keyboard(menu_id, items)
            if menu["status"] == "open"
            else None
        )
        self.telegram.send_message(
            chat_id, private_order_text(menu, items, order), keyboard
        )

    def show_personal_status(self, chat_id: int, user_id: int) -> None:
        group_id = self._configured_group()
        order = (
            self.db.latest_order_for_user(group_id, user_id)
            if group_id is not None
            else None
        )
        if not order:
            self.telegram.send_message(chat_id, "Hozircha buyurtmangiz yo‘q.")
            return
        self.telegram.send_message(
            chat_id,
            f"<b>🍽 Sizning buyurtmangiz</b>\n\n"
            f"Taom: <b>{escape(order['meal_name'])}</b>\n"
            f"Narx: <b>{money(order['price'])}</b>\n"
            f"To‘lov: <b>{PAYMENT_LABELS[order['payment_status']]}</b>",
        )

    def show_admin_panel(self, chat_id: int, user_id: int, menu_id: int) -> None:
        menu = self.db.get_menu(menu_id)
        if not menu or not self._is_group_admin(menu["chat_id"], user_id):
            self.telegram.send_message(chat_id, "Faqat guruh admini uchun.")
            return
        self.telegram.send_message(
            chat_id, summary_text(self.db.order_summary(menu_id), "🧾 Admin panel")
        )
        payments = self.db.payments_for_menu(menu_id)
        if not payments:
            self.telegram.send_message(chat_id, "Hozircha tekshiriladigan to‘lov yo‘q.")
            return
        for payment in payments:
            extracted = (
                money(payment["extracted_amount"])
                if payment["extracted_amount"] is not None
                else "o‘qilmadi"
            )
            caption = (
                f"<b>🧾 {escape(payment['first_name'])} — "
                f"{escape(payment['meal_name'])}</b>\n"
                f"Kutilgan: <b>{money(payment['expected_amount'])}</b>\n"
                f"Chekda: <b>{extracted}</b>\n"
                f"Holat: <b>{PAYMENT_LABELS[payment['status']]}</b>"
            )
            if payment["telegram_file_id"]:
                self.telegram.send_photo(
                    chat_id,
                    payment["telegram_file_id"],
                    caption,
                    payment_keyboard(payment["id"]),
                )
            else:
                self.telegram.send_message(
                    chat_id, caption, payment_keyboard(payment["id"])
                )
