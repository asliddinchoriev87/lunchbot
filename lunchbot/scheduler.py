from __future__ import annotations

from datetime import date, datetime, time


def parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def due_action(
    now: datetime,
    menu_date: date,
    status: str,
    reminder_times: tuple[str, ...],
    sent_labels: set[str],
) -> tuple[str, tuple[str, ...]] | None:
    if status != "open" or now.date() != menu_date:
        return None

    due = [label for label in reminder_times if parse_clock(label) <= now.time()]
    if not due:
        return None
    latest = due[-1]
    if latest in sent_labels:
        return None
    return ("reminder", tuple(due))
