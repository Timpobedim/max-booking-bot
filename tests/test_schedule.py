"""Свободное время: загрузка подъёмников, часы работы, запись не раньше чем через полчаса."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from tyre_bot.bot import normalize_phone
from tyre_bot.schedule import SERVICES, free_slots, max_load
from tyre_bot.storage import SlotTakenError, Storage

DAY = date(2026, 9, 14)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute))


def test_back_to_back_bookings_leave_room_for_a_long_one() -> None:
    bookings = [(at(9), at(9, 30)), (at(9, 30), at(10))]

    assert max_load(bookings, at(9), at(10)) == 1  # при двух подъёмниках 9:00–10:00 ещё свободно


def test_overlapping_bookings_take_both_posts() -> None:
    assert max_load([(at(9), at(10)), (at(9, 30), at(10, 30))], at(9, 30), at(10)) == 2


def test_slots_respect_capacity_and_closing_time() -> None:
    busy = [(at(9), at(10)), (at(9), at(10))]

    slots = free_slots(DAY, SERVICES["season_big"], busy, opens=time(9), closes=time(20), posts=2, now=at(8))

    assert slots[0] == at(10)  # 9:00 и 9:30 заняты на обоих подъёмниках
    assert slots[-1] == at(19)  # часовая услуга должна закончиться к закрытию в 20:00
    assert all(slot.minute in (0, 30) for slot in slots)


def test_no_booking_for_the_next_half_hour() -> None:
    slots = free_slots(DAY, SERVICES["puncture"], [], opens=time(9), closes=time(20), posts=2, now=at(12, 10))

    assert slots[0] == at(13)


@pytest.mark.parametrize(
    ("text", "phone"),
    [
        ("8 (900) 123-45-67", "+79001234567"),
        ("+7 900 1234567", "+79001234567"),
        ("9001234567", "+79001234567"),
        ("завтра после обеда", None),
        ("123-45-67", None),
    ],
)
def test_phone_numbers_are_normalized(text: str, phone: str | None) -> None:
    assert normalize_phone(text) == phone


def test_same_booking_twice_is_one_record(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bookings.sqlite3")
    booking = {
        "user_id": 1,
        "name": "Анна",
        "phone": "+79001234567",
        "service": "puncture",
        "starts_at": at(9),
        "posts": 1,
    }

    first = storage.book(**booking)  # type: ignore[arg-type]
    second = storage.book(**booking)  # type: ignore[arg-type]

    assert first.id == second.id
    assert len(storage.upcoming(1, at(8))) == 1


def test_full_slot_is_refused(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bookings.sqlite3")
    storage.book(user_id=1, name="Анна", phone="+79001234567", service="puncture", starts_at=at(9), posts=1)

    with pytest.raises(SlotTakenError):
        storage.book(user_id=2, name="Олег", phone="+79007654321", service="season", starts_at=at(9), posts=1)
