import unittest
from datetime import date

from lunchbot.parsing import looks_like_menu, parse_menu


SAMPLE_MENU = """02.09.2026 🗓

Меню:🧾

Порция 35 000 сум🧾

1️⃣ Стейк беатс
2️⃣ Ассорти шашлык
3️⃣ Киевский котелт
4️⃣ Жареный лагмон
5️⃣ Мастава + самса

Салат (бесплатно)
Доставка 20 000 сум
Более 4-х комплект бесплатно

5614 6816 0000 0000
+998990000000
"""


class MenuParserTests(unittest.TestCase):
    def test_sample_menu_is_extracted(self):
        menu = parse_menu(SAMPLE_MENU, today=date(2026, 9, 2))

        self.assertEqual(menu.menu_date, date(2026, 9, 2))
        self.assertEqual(menu.portion_price, 35_000)
        self.assertEqual(menu.delivery_fee, 20_000)
        self.assertEqual(menu.free_delivery_min, 5)
        self.assertEqual(
            menu.items,
            (
                "Стейк беатс",
                "Ассорти шашлык",
                "Киевский котелт",
                "Жареный лагмон",
                "Мастава + самса",
            ),
        )

    def test_menu_detection(self):
        self.assertTrue(looks_like_menu(SAMPLE_MENU))
        self.assertFalse(looks_like_menu("Bugun nima yeymiz?"))

    def test_sensitive_numbers_are_redacted_before_storage(self):
        menu = parse_menu(SAMPLE_MENU, today=date(2026, 9, 2))
        self.assertNotIn("5614 6816 0000 0000", menu.raw_text)
        self.assertNotIn("+998990000000", menu.raw_text)
        self.assertIn("[CARD REDACTED]", menu.raw_text)
        self.assertIn("[PHONE REDACTED]", menu.raw_text)


if __name__ == "__main__":
    unittest.main()
