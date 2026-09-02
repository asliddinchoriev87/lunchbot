from __future__ import annotations

from collections import OrderedDict
from html import escape

from .domain import OrderSummary, ParsedMenu


PAYMENT_LABELS = {
    "unpaid": "❌ To‘lanmagan",
    "needs_review": "🟡 Tekshirish kerak",
    "ai_matched": "☑️ AI moslashtirdi",
    "verified": "✅ Tasdiqlangan",
    "rejected": "⛔ Rad etilgan",
}

PAYMENT_ICONS = {
    "unpaid": "❌",
    "needs_review": "🟡",
    "ai_matched": "☑️",
    "verified": "✅",
    "rejected": "⛔",
}


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " so‘m"


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


def menu_preview(menu: ParsedMenu) -> str:
    items = "\n".join(f"{index}. {escape(name)}" for index, name in enumerate(menu.items, 1))
    delivery = money(menu.delivery_fee) if menu.delivery_fee else "ko‘rsatilmagan"
    free = (
        f"{menu.free_delivery_min} ta porsiyadan boshlab"
        if menu.free_delivery_min
        else "ko‘rsatilmagan"
    )
    return (
        "<b>🧾 Menyu topildi — tekshiring</b>\n\n"
        f"Sana: <b>{menu.menu_date.strftime('%d.%m.%Y')}</b>\n"
        f"Porsiya: <b>{money(menu.portion_price)}</b>\n\n"
        f"{items}\n\n"
        f"Yetkazib berish: {delivery}\n"
        f"Bepul yetkazish: {free}"
    )


def private_order_text(menu_row, items: list, order=None) -> str:
    item_lines = "\n".join(
        f"{row['position']}. {escape(row['name'])}" for row in items
    )
    selected = (
        f"\n\nSizning tanlovingiz: <b>{escape(order['meal_name'])}</b>\n"
        f"To‘lov: <b>{PAYMENT_LABELS[order['payment_status']]}</b>"
        if order
        else "\n\nHali taom tanlamadingiz."
    )
    status = "ochiq" if menu_row["status"] == "open" else "yopilgan"
    instruction = (
        "Taomni quyidagi tugmalardan tanlang. Chek rasmini shu shaxsiy chatga yuboring."
        if menu_row["status"] == "open"
        else "Buyurtma yopilgan. To‘lov chekini shu shaxsiy chatga yuborishingiz mumkin."
    )
    return (
        f"<b>🍽 Bugungi tushlik — {menu_row['menu_date']}</b>\n\n"
        f"{item_lines}\n\n"
        f"Narx: <b>{money(menu_row['portion_price'])}</b>\n"
        f"Holat: <b>{status}</b>"
        f"{selected}\n\n"
        f"{instruction}"
    )


def private_order_keyboard(menu_id: int, items: list) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": f"{row['position']}. {row['name']}",
                    "callback_data": f"order:{menu_id}:{row['id']}",
                }
            ]
            for row in items
        ]
    }


def group_dashboard_text(menu_row, items: list, summary: OrderSummary) -> str:
    item_lines = "\n".join(
        f"{row['position']}. {escape(row['name'])}" for row in items
    )
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for row in summary.rows:
        grouped.setdefault(row["meal_name"], []).append(row)

    sections: list[str] = []
    for meal, rows in grouped.items():
        people = ", ".join(
            f"{escape(row['first_name'][:24])} {PAYMENT_ICONS[row['payment_status']]}"
            for row in rows
        )
        sections.append(f"<b>{escape(meal)} — {len(rows)} ta</b>\n{people}")
    orders = "\n\n".join(sections) if sections else "Hali buyurtma yo‘q."
    delivery = "Bepul" if summary.applied_delivery_fee == 0 else money(summary.applied_delivery_fee)
    state = "🟢 Ochiq" if menu_row["status"] == "open" else "🔒 Yopilgan"
    return (
        f"<b>🍽 Bugungi tushlik — {menu_row['menu_date']}</b>\n"
        f"Holat: <b>{state}</b>\n\n"
        f"{item_lines}\n\n"
        f"Narx: <b>{money(menu_row['portion_price'])}</b>\n\n"
        f"<b>📋 Buyurtmalar va to‘lovlar</b>\n{orders}\n\n"
        f"Porsiyalar: <b>{summary.portion_count}</b> | "
        f"Jami: <b>{money(summary.grand_total)}</b> | "
        f"Yetkazish: <b>{delivery}</b>\n"
        "To‘lov: ❌ yo‘q · 🟡 tekshirish · ☑️ AI mos · ✅ tasdiq"
    )


def group_dashboard_keyboard(menu_id: int, bot_username: str, is_open: bool) -> dict:
    rows = []
    if is_open:
        rows.append(
            [
                {
                    "text": "🍽 Taom tanlash — shaxsiy chat",
                    "url": f"https://t.me/{bot_username}?start=order_{menu_id}",
                }
            ]
        )
        rows.append(
            [
                {
                    "text": "📋 Full orders (admin)",
                    "url": f"https://t.me/{bot_username}?start=fullorders_{menu_id}",
                },
                {
                    "text": "🧾 To‘lovlarni tekshirish",
                    "url": f"https://t.me/{bot_username}?start=admin_{menu_id}",
                }
            ]
        )
        rows.append(
            [
                {
                    "text": "🔒 Buyurtmani yopish",
                    "callback_data": f"menu_close:{menu_id}",
                }
            ]
        )
    else:
        rows.append(
            [
                {
                    "text": "📋 Full orders (admin)",
                    "url": f"https://t.me/{bot_username}?start=fullorders_{menu_id}",
                },
                {
                    "text": "🧾 To‘lovlarni tekshirish",
                    "url": f"https://t.me/{bot_username}?start=admin_{menu_id}",
                }
            ]
        )
    return {"inline_keyboard": rows}


def full_orders_pages(summary: OrderSummary, page_size: int = 35) -> list[str]:
    rows = summary.rows
    page_count = max(1, (len(rows) + page_size - 1) // page_size)
    verified = sum(row["payment_status"] == "verified" for row in rows)
    ai_matched = sum(row["payment_status"] == "ai_matched" for row in rows)
    review = sum(row["payment_status"] == "needs_review" for row in rows)
    unpaid = sum(row["payment_status"] == "unpaid" for row in rows)
    pages: list[str] = []
    for page_index in range(page_count):
        start = page_index * page_size
        page_rows = rows[start : start + page_size]
        lines = [
            f"{start + index}. <b>{escape(row['first_name'][:40])}</b> — "
            f"{escape(row['meal_name'][:60])} — {PAYMENT_LABELS[row['payment_status']]}"
            for index, row in enumerate(page_rows, 1)
        ]
        body = "\n".join(lines) if lines else "Hali buyurtma yo‘q."
        page_label = f" ({page_index + 1}/{page_count})" if page_count > 1 else ""
        pages.append(
            f"<b>📋 Full orders — {escape(summary.menu_date)}{page_label}</b>\n\n"
            f"{body}\n\n"
            f"Jami: <b>{summary.portion_count}</b> · "
            f"✅ {verified} · ☑️ {ai_matched} · 🟡 {review} · ❌ {unpaid}"
        )
    return pages


def registration_keyboard(bot_username: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Botni shaxsiy chatda ochish",
                    "url": f"https://t.me/{bot_username}?start=register",
                }
            ]
        ]
    }


def admin_menu_keyboard(menu_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Tasdiqlash", "callback_data": f"menu_confirm:{menu_id}"},
                {"text": "❌ Bekor qilish", "callback_data": f"menu_cancel:{menu_id}"},
            ]
        ]
    }


def payment_keyboard(payment_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Tasdiqlash", "callback_data": f"payment_verify:{payment_id}"},
                {"text": "⛔ Rad etish", "callback_data": f"payment_reject:{payment_id}"},
            ]
        ]
    }


def summary_text(summary: OrderSummary, title: str = "📦 Buyurtmalar") -> str:
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for row in summary.rows:
        grouped.setdefault(row["meal_name"], []).append(row)

    sections: list[str] = []
    for meal, rows in grouped.items():
        names = ", ".join(escape(row["first_name"]) for row in rows)
        sections.append(f"<b>{escape(meal)} — {len(rows)} ta</b>\n{names}")
    body = "\n\n".join(sections) if sections else "Hali buyurtma yo‘q."
    verified = sum(row["payment_status"] == "verified" for row in summary.rows)
    matched = sum(row["payment_status"] == "ai_matched" for row in summary.rows)
    delivery = "Bepul" if summary.applied_delivery_fee == 0 else money(summary.applied_delivery_fee)
    return (
        f"<b>{title} — {escape(summary.menu_date)}</b>\n\n"
        f"{body}\n\n"
        f"Porsiyalar: <b>{summary.portion_count}</b>\n"
        f"Taomlar: <b>{money(summary.food_total)}</b>\n"
        f"Yetkazish: <b>{delivery}</b>\n"
        f"Jami: <b>{money(summary.grand_total)}</b>\n"
        f"To‘lov: <b>{verified} tasdiqlangan</b>, {matched} AI moslashtirgan"
    )


def caterer_text(summary: OrderSummary) -> str:
    counts: OrderedDict[str, int] = OrderedDict()
    for row in summary.rows:
        counts[row["meal_name"]] = counts.get(row["meal_name"], 0) + 1
    lines = "\n".join(
        f"{index}. {escape(meal)} — <b>{count} ta</b>"
        for index, (meal, count) in enumerate(counts.items(), 1)
    )
    if not lines:
        lines = "Buyurtma yo‘q."
    return (
        "<b>👨‍🍳 Oshxonaga yuboriladigan buyurtma</b>\n\n"
        f"Sana: {escape(summary.menu_date)}\n"
        f"{lines}\n\n"
        f"Jami: <b>{summary.portion_count} ta porsiya</b>"
    )
