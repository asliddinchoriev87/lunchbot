from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from datetime import date, datetime

from .domain import ParsedMenu, ReceiptAnalysis
from .parsing import redact_sensitive


class AIError(RuntimeError):
    pass


class AIClient:
    def __init__(self, api_key: str | None, model: str):
        self.api_key = api_key
        self.model = model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _response(self, *, instructions: str, content: list[dict], schema: dict) -> dict:
        if not self.api_key:
            raise AIError("OPENAI_API_KEY is not configured")
        payload = {
            "model": self.model,
            "store": False,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema["name"],
                    "strict": True,
                    "schema": schema["schema"],
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise AIError(f"OpenAI HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise AIError(f"OpenAI network error: {exc.reason}") from exc

        for item in result.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    try:
                        return json.loads(block["text"])
                    except (KeyError, json.JSONDecodeError) as exc:
                        raise AIError("OpenAI returned invalid structured output") from exc
        raise AIError("OpenAI returned no structured output")

    def extract_menu(self, text: str, today: date) -> ParsedMenu:
        schema = {
            "name": "daily_menu",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "menu_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "items": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "portion_price": {"type": "integer", "minimum": 1},
                    "delivery_fee": {"type": "integer", "minimum": 0},
                    "free_delivery_min": {"type": ["integer", "null"]},
                },
                "required": [
                    "menu_date",
                    "items",
                    "portion_price",
                    "delivery_fee",
                    "free_delivery_min",
                ],
            },
        }
        data = self._response(
            instructions=(
                "Extract a food menu exactly. Preserve meal names. Convert Uzbek sum prices to integers. "
                "If the menu says delivery is free for more than N portions, free_delivery_min is N+1."
            ),
            content=[
                {
                    "type": "input_text",
                    "text": f"Today is {today.isoformat()}. Extract this menu:\n\n{text}",
                }
            ],
            schema=schema,
        )
        return ParsedMenu(
            menu_date=datetime.strptime(data["menu_date"], "%Y-%m-%d").date(),
            items=tuple(data["items"]),
            portion_price=int(data["portion_price"]),
            delivery_fee=int(data["delivery_fee"]),
            free_delivery_min=data["free_delivery_min"],
            raw_text=redact_sensitive(text),
        )

    def analyze_receipt(self, image: bytes, mime_type: str = "image/jpeg") -> ReceiptAnalysis:
        encoded = base64.b64encode(image).decode("ascii")
        schema = {
            "name": "payment_receipt",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "amount": {"type": ["integer", "null"]},
                    "recipient_name": {"type": ["string", "null"]},
                    "transaction_time": {"type": ["string", "null"]},
                    "success_visible": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "note": {"type": "string"},
                },
                "required": [
                    "amount",
                    "recipient_name",
                    "transaction_time",
                    "success_visible",
                    "confidence",
                    "note",
                ],
            },
        }
        data = self._response(
            instructions=(
                "Read only visible payment-receipt facts. Do not guess hidden details. "
                "The amount must be an integer in Uzbek sum. success_visible is true only when "
                "the image visibly states that the transfer succeeded. Return transaction_time "
                "as YYYY-MM-DD HH:MM when both date and time are visible; otherwise return null."
            ),
            content=[
                {"type": "input_text", "text": "Extract the payment receipt fields."},
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": "high",
                },
            ],
            schema=schema,
        )
        return ReceiptAnalysis(
            amount=data["amount"],
            recipient_name=data["recipient_name"],
            transaction_time=data["transaction_time"],
            success_visible=bool(data["success_visible"]),
            confidence=float(data["confidence"]),
            note=data["note"],
        )
