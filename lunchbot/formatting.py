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


def ordering_text(menu_row, items: list) -> str:
    item_lines = "\n".join(
        f"{row['position']}. {escape(row['name'])}" for row in items
    )
    return (
        f"<b>🍽 Bugungi tushlik — {menu_row['menu_date']}</b>\n\n"
        f"{item_lines}\n\n"
        f"Narx: <b>{money(menu_row['portion_price'])}</b>\n"
        "Buyurtma <b>11:00</b> da yopiladi. Taomni tugma orqali tanlang. "
        "Tanlovni 11:00 gacha o‘zgartirish mumkin."
    )


def order_keyboard(menu_id: int, items: list) -> dict:
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


def registration_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✅ Ro‘yxatdan o‘tish", "callback_data": "register"}]
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
