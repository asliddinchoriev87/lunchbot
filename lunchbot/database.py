from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from .domain import OrderSummary, ParsedMenu, ReceiptAnalysis


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    username TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS menus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    menu_date TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    portion_price INTEGER NOT NULL,
    delivery_fee INTEGER NOT NULL DEFAULT 0,
    free_delivery_min INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    order_message_id INTEGER,
    source_message_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_id INTEGER NOT NULL REFERENCES menus(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    price INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_id INTEGER NOT NULL REFERENCES menus(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    item_id INTEGER NOT NULL REFERENCES menu_items(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(menu_id, user_id)
);
CREATE TABLE IF NOT EXISTS reminders (
    menu_id INTEGER NOT NULL REFERENCES menus(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY(menu_id, label)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    file_unique_id TEXT,
    image_hash TEXT NOT NULL UNIQUE,
    expected_amount INTEGER NOT NULL,
    extracted_amount INTEGER,
    recipient_name TEXT,
    transaction_time TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    success_visible INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_menus_chat_date ON menus(chat_id, menu_date);
CREATE INDEX IF NOT EXISTS idx_orders_menu ON orders(menu_id);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_schema()
        self.connection.commit()

    def _migrate_schema(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(menus)").fetchall()
        }
        if "source_message_id" not in columns:
            self.connection.execute(
                "ALTER TABLE menus ADD COLUMN source_message_id INTEGER"
            )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_menus_source "
            "ON menus(chat_id, source_message_id) WHERE source_message_id IS NOT NULL"
        )

    def close(self) -> None:
        self.connection.close()

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def upsert_user(self, user_id: int, first_name: str, username: str | None) -> None:
        self.connection.execute(
            """INSERT INTO users(user_id, first_name, username, active, registered_at)
               VALUES(?, ?, ?, 1, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 first_name=excluded.first_name, username=excluded.username, active=1""",
            (user_id, first_name or "Telegram user", username, utc_now()),
        )
        self.connection.commit()

    def create_menu(
        self, chat_id: int, menu: ParsedMenu, source_message_id: int | None = None
    ) -> int | None:
        if source_message_id is not None:
            existing = self.connection.execute(
                "SELECT id FROM menus WHERE chat_id=? AND source_message_id=?",
                (chat_id, source_message_id),
            ).fetchone()
            if existing:
                return None
        try:
            cursor = self.connection.execute(
                """INSERT INTO menus(
                     chat_id, menu_date, raw_text, portion_price, delivery_fee,
                     free_delivery_min, status, source_message_id, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (
                    chat_id,
                    menu.menu_date.isoformat(),
                    menu.raw_text,
                    menu.portion_price,
                    menu.delivery_fee,
                    menu.free_delivery_min,
                    source_message_id,
                    utc_now(),
                ),
            )
        except sqlite3.IntegrityError:
            self.connection.rollback()
            return None
        menu_id = int(cursor.lastrowid)
        self.connection.execute(
            "UPDATE menus SET status='cancelled' "
            "WHERE chat_id=? AND menu_date=? AND status='draft' AND id<>?",
            (chat_id, menu.menu_date.isoformat(), menu_id),
        )
        self.connection.executemany(
            "INSERT INTO menu_items(menu_id, position, name, price) VALUES(?, ?, ?, ?)",
            [(menu_id, index, name, menu.portion_price) for index, name in enumerate(menu.items, 1)],
        )
        self.connection.commit()
        return menu_id

    def get_menu(self, menu_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM menus WHERE id=?", (menu_id,)).fetchone()

    def get_menu_items(self, menu_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM menu_items WHERE menu_id=? ORDER BY position", (menu_id,)
            ).fetchall()
        )

    def confirm_menu(self, menu_id: int) -> bool:
        menu = self.get_menu(menu_id)
        if not menu or menu["status"] != "draft":
            return False
        self.connection.execute(
            "UPDATE menus SET status='cancelled' WHERE chat_id=? AND status='open'",
            (menu["chat_id"],),
        )
        self.connection.execute("UPDATE menus SET status='open' WHERE id=?", (menu_id,))
        self.connection.commit()
        return True

    def set_order_message_id(self, menu_id: int, message_id: int) -> None:
        self.connection.execute(
            "UPDATE menus SET order_message_id=? WHERE id=?", (message_id, menu_id)
        )
        self.connection.commit()

    def cancel_menu(self, menu_id: int) -> None:
        self.connection.execute("UPDATE menus SET status='cancelled' WHERE id=?", (menu_id,))
        self.connection.commit()

    def latest_menu(self, chat_id: int, statuses: tuple[str, ...] = ("open",)) -> sqlite3.Row | None:
        placeholders = ",".join("?" for _ in statuses)
        return self.connection.execute(
            f"SELECT * FROM menus WHERE chat_id=? AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (chat_id, *statuses),
        ).fetchone()

    def open_menus(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM menus WHERE status='open'").fetchall())

    def set_order(self, menu_id: int, user_id: int, item_id: int) -> None:
        item = self.connection.execute(
            "SELECT id FROM menu_items WHERE id=? AND menu_id=?", (item_id, menu_id)
        ).fetchone()
        if not item:
            raise ValueError("Meal does not belong to this menu")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO orders(menu_id, user_id, item_id, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(menu_id, user_id) DO UPDATE SET
                 item_id=excluded.item_id, updated_at=excluded.updated_at""",
            (menu_id, user_id, item_id, now, now),
        )
        self.connection.commit()

    def users_without_order(self, menu_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT u.* FROM users u
                   WHERE u.active=1 AND NOT EXISTS(
                     SELECT 1 FROM orders o WHERE o.menu_id=? AND o.user_id=u.user_id
                   ) ORDER BY u.first_name COLLATE NOCASE""",
                (menu_id,),
            ).fetchall()
        )

    def sent_reminders(self, menu_id: int) -> set[str]:
        return {
            row["label"]
            for row in self.connection.execute(
                "SELECT label FROM reminders WHERE menu_id=?", (menu_id,)
            ).fetchall()
        }

    def mark_reminders(self, menu_id: int, labels: tuple[str, ...]) -> None:
        self.connection.executemany(
            "INSERT OR IGNORE INTO reminders(menu_id, label, sent_at) VALUES(?, ?, ?)",
            [(menu_id, label, utc_now()) for label in labels],
        )
        self.connection.commit()

    def close_menu(self, menu_id: int) -> None:
        self.connection.execute("UPDATE menus SET status='closed' WHERE id=?", (menu_id,))
        self.connection.commit()

    def order_summary(self, menu_id: int) -> OrderSummary:
        menu = self.get_menu(menu_id)
        if not menu:
            raise ValueError("Menu not found")
        rows = list(
            self.connection.execute(
                """SELECT o.id AS order_id, u.user_id, u.first_name, u.username,
                          mi.name AS meal_name, mi.price, o.updated_at,
                          COALESCE((
                            SELECT p.status FROM payments p
                            WHERE p.order_id=o.id ORDER BY p.id DESC LIMIT 1
                          ), 'unpaid') AS payment_status
                   FROM orders o
                   JOIN users u ON u.user_id=o.user_id
                   JOIN menu_items mi ON mi.id=o.item_id
                   WHERE o.menu_id=?
                   ORDER BY mi.position, u.first_name COLLATE NOCASE""",
                (menu_id,),
            ).fetchall()
        )
        return OrderSummary(
            menu_id=menu_id,
            menu_date=menu["menu_date"],
            portion_price=menu["portion_price"],
            delivery_fee=menu["delivery_fee"],
            free_delivery_min=menu["free_delivery_min"],
            rows=[dict(row) for row in rows],
        )

    def latest_order_for_user(self, chat_id: int, user_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT o.id AS order_id, o.menu_id, mi.price, mi.name AS meal_name, m.menu_date
               FROM orders o
               JOIN menus m ON m.id=o.menu_id
               JOIN menu_items mi ON mi.id=o.item_id
               WHERE m.chat_id=? AND o.user_id=? AND m.status IN ('open','closed')
               ORDER BY m.id DESC LIMIT 1""",
            (chat_id, user_id),
        ).fetchone()

    def add_payment(
        self,
        order_id: int,
        file_unique_id: str | None,
        image_hash: str,
        expected_amount: int,
        analysis: ReceiptAnalysis,
        status: str,
        note: str,
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO payments(
                 order_id, file_unique_id, image_hash, expected_amount, extracted_amount,
                 recipient_name, transaction_time, confidence, success_visible,
                 status, note, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                file_unique_id,
                image_hash,
                expected_amount,
                analysis.amount,
                analysis.recipient_name,
                analysis.transaction_time,
                analysis.confidence,
                int(analysis.success_visible),
                status,
                note,
                utc_now(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_payment_status(self, payment_id: int, status: str) -> None:
        self.connection.execute("UPDATE payments SET status=? WHERE id=?", (status, payment_id))
        self.connection.commit()

    def get_payment(self, payment_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT p.*, u.first_name, mi.name AS meal_name
               FROM payments p
               JOIN orders o ON o.id=p.order_id
               JOIN users u ON u.user_id=o.user_id
               JOIN menu_items mi ON mi.id=o.item_id
               WHERE p.id=?""",
            (payment_id,),
        ).fetchone()
