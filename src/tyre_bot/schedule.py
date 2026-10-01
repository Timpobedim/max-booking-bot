"""Услуги и свободное время: сетка по 30 минут, несколько подъёмников, запись не позже чем за полчаса."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

STEP = timedelta(minutes=30)
LEAD_TIME = timedelta(minutes=30)  # на ближайшие полчаса уже не записываем — мастер не успеет подготовиться
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


@dataclass(frozen=True)
class Service:
    key: str
    title: str
    minutes: int
    price: int  # рубли

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.minutes)


SERVICES = {
    service.key: service
    for service in (
        Service("season", "Сезонная замена колёс R13–R16", 40, 2400),
        Service("season_big", "Сезонная замена колёс R17–R22", 60, 3600),
        Service("balance", "Балансировка четырёх колёс", 30, 1600),
        Service("puncture", "Ремонт прокола", 30, 800),
    )
}


def max_load(bookings: Iterable[tuple[datetime, datetime]], start: datetime, end: datetime) -> int:
    """Наибольшее число записей, идущих одновременно в какой-то момент промежутка [start, end).

    Считать просто пересекающиеся записи нельзя: 9:00–9:30 и 9:30–10:00 не мешают друг другу,
    и при двух подъёмниках запись на 9:00–10:00 между ними помещается.
    """
    events: list[tuple[datetime, int]] = []
    for booked_start, booked_end in bookings:
        if booked_start < end and start < booked_end:
            events += [(max(booked_start, start), 1), (min(booked_end, end), -1)]
    load = peak = 0
    for _, change in sorted(events, key=lambda event: (event[0], event[1])):  # окончание раньше начала
        load += change
        peak = max(peak, load)
    return peak


def free_slots(
    day: date,
    service: Service,
    bookings: list[tuple[datetime, datetime]],
    *,
    opens: time,
    closes: time,
    posts: int,
    now: datetime,
) -> list[datetime]:
    slots = []
    start, closing = datetime.combine(day, opens), datetime.combine(day, closes)
    while start + service.duration <= closing:
        if start >= now + LEAD_TIME and max_load(bookings, start, start + service.duration) < posts:
            slots.append(start)
        start += STEP
    return slots


def label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"
