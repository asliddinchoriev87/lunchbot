from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.file_url = f"https://api.telegram.org/file/bot{token}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        payload = payload or {}
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        network_timeout = max(20, int(payload.get("timeout", 0)) + 10)
        try:
            with urllib.request.urlopen(request, timeout=network_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise TelegramError(f"Telegram HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"Telegram network error: {exc.reason}") from exc
        if not data.get("ok"):
            raise TelegramError(data.get("description", "Unknown Telegram error"))
        return data.get("result")

    def get_updates(self, offset: int | None, timeout: int = 10) -> list[dict]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload)

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return self.call("sendMessage", payload)

    def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageText", payload)

    def answer_callback(self, callback_query_id: str, text: str = "", alert: bool = False) -> None:
        self.call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text, "show_alert": alert},
        )

    def is_admin(self, chat_id: int, user_id: int) -> bool:
        member = self.call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        return member.get("status") in {"creator", "administrator"}

    def download_file(self, file_id: str) -> bytes:
        file_info = self.call("getFile", {"file_id": file_id})
        path = file_info["file_path"]
        try:
            with urllib.request.urlopen(f"{self.file_url}/{path}", timeout=30) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise TelegramError(f"Could not download receipt: {exc.reason}") from exc
