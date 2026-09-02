from __future__ import annotations

import re
from datetime import date, datetime

from .domain import ParsedMenu


DATE_RE = re.compile(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b")
PRICE_RE = re.compile(r"(?:порци[яи]|нархи|price)\D{0,20}([\d\s.,]+)\s*(?:сум|so['’]?m)", re.I)
DELIVERY_RE = re.compile(r"(?:доставка|yetkazib\s*berish|delivery)\D{0,20}([\d\s.,]+)\s*(?:сум|so['’]?m)", re.I)
FREE_DELIVERY_RE = re.compile(r"(?:более|ko['’]?p|more\s+than)\s*(\d+)\s*[-–]?\s*(?:х|ta)?", re.I)
NUMBERED_ITEM_RE = re.compile(
    r"^\s*(?:[0-9]\ufe0f?\u20e3\s*|\d{1,2}[\s.)\-–:]+)(.+?)\s*$"
)
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){15}\d(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?998|0)[\d ()-]{7,14}(?!\d)")


def _money(value: str) -> int:
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else 0


def redact_sensitive(text: str) -> str:
    text = CARD_RE.sub("[CARD REDACTED]", text)
    return PHONE_RE.sub("[PHONE REDACTED]", text)


def looks_like_menu(text: str) -> bool:
    lowered = text.casefold()
    numbered_lines = sum(bool(NUMBERED_ITEM_RE.match(line)) for line in text.splitlines())
    return numbered_lines >= 2 and any(marker in lowered for marker in ("меню", "порция", "menu"))


def parse_menu(text: str, today: date | None = None) -> ParsedMenu:
    today = today or date.today()
    date_match = DATE_RE.search(text)
    if date_match:
        menu_date = datetime.strptime(date_match.group(0).replace("/", ".").replace("-", "."), "%d.%m.%Y").date()
    else:
        menu_date = today

    price_match = PRICE_RE.search(text)
    if not price_match:
        raise ValueError("Portion price was not found")
    portion_price = _money(price_match.group(1))
    if portion_price <= 0:
        raise ValueError("Portion price is invalid")

    items: list[str] = []
    for line in text.splitlines():
        if DATE_RE.search(line):
            continue
        match = NUMBERED_ITEM_RE.match(line)
        if not match:
            continue
        item = match.group(1).strip(" \t-–—.,")
        if item and not re.fullmatch(r"[\d\s.,+-]+", item):
            items.append(item)

    if len(items) < 2:
        raise ValueError("At least two meals are required")
    if len(items) > 20:
        raise ValueError("Too many numbered menu items")

    delivery_match = DELIVERY_RE.search(text)
    delivery_fee = _money(delivery_match.group(1)) if delivery_match else 0
    free_match = FREE_DELIVERY_RE.search(text)
    free_delivery_min = int(free_match.group(1)) + 1 if free_match else None

    return ParsedMenu(
        menu_date=menu_date,
        items=tuple(items),
        portion_price=portion_price,
        delivery_fee=delivery_fee,
        free_delivery_min=free_delivery_min,
        raw_text=redact_sensitive(text),
    )
