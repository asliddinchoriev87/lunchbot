import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lunchbot.scheduler import due_action


TZ = ZoneInfo("Asia/Tashkent")


class SchedulerTests(unittest.TestCase):
    def test_nothing_is_due_before_first_reminder(self):
        now = datetime(2026, 9, 2, 10, 19, tzinfo=TZ)
        action = due_action(now, date(2026, 9, 2), "open", ("10:20", "10:35", "10:50"), "11:00", set())
        self.assertIsNone(action)

    def test_latest_due_reminder_is_returned_without_backlog_spam(self):
        now = datetime(2026, 9, 2, 10, 40, tzinfo=TZ)
        action = due_action(now, date(2026, 9, 2), "open", ("10:20", "10:35", "10:50"), "11:00", {"10:20"})
        self.assertEqual(action, ("reminder", ("10:20", "10:35")))

    def test_order_closes_at_eleven(self):
        now = datetime(2026, 9, 2, 11, 0, tzinfo=TZ)
        action = due_action(now, date(2026, 9, 2), "open", ("10:20", "10:35", "10:50"), "11:00", set())
        self.assertEqual(action, ("close", ()))

    def test_stale_open_menu_is_closed(self):
        now = datetime(2026, 9, 3, 9, 0, tzinfo=TZ)
        action = due_action(now, date(2026, 9, 2), "open", ("10:20",), "11:00", set())
        self.assertEqual(action, ("close", ()))


if __name__ == "__main__":
    unittest.main()
