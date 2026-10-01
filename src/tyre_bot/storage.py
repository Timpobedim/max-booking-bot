"""Записи и черновики в SQLite.

Запись создаётся в транзакции `BEGIN IMMEDIATE`: проверка свободного места и вставка идут под
блокировкой базы, поэтому два клиента, нажавшие на одно время, не займут один подъёмник дважды.
Уникальный индекс (клиент, время) делает запись идемпотентной: событие, пришедшее повторно после
перезапуска бота, не создаст вторую запись.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from tyre_bot.schedule import SERVICES, max_load

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    service TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_client_and_time
    ON bookings (user_id, starts_at) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS drafts (
    user_id INTEGER PRIMARY KEY,
    service TEXT,
    day TEXT,
    slot TEXT
);
"""


DAY_BOOKINGS = (
    "SELECT starts_at, ends_at FROM bookings WHERE status = 'active' AND substr(starts_at, 1, 10) = ?"
)


class SlotTakenError(Exception):
    """Пока клиент вводил телефон, время заняли."""


@dataclass(frozen=True)
class Booking:
    id: int
    user_id: int
    name: str
    phone: str
    service: str
    starts_at: datetime
    ends_at: datetime

    @property
    def title(self) -> str:
        return SERVICES[self.service].title


@dataclass(frozen=True)
class Draft:
    service: str | None
    day: date | None
    slot: datetime | None


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, isolation_level=None)  # транзакции — явно
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    # --- черновик: что клиент уже выбрал -----------------------------------------------------------

    def draft(self, user_id: int) -> Draft:
        with self._connect() as db:
            row = db.execute("SELECT service, day, slot FROM drafts WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return Draft(None, None, None)
        return Draft(
            row["service"],
            date.fromisoformat(row["day"]) if row["day"] else None,
            datetime.fromisoformat(row["slot"]) if row["slot"] else None,
        )

    def save_draft(
        self,
        user_id: int,
        *,
        service: str | None = None,
        day: date | None = None,
        slot: datetime | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO drafts (user_id, service, day, slot) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "service = excluded.service, day = excluded.day, slot = excluded.slot",
                (user_id, service, day.isoformat() if day else None, slot.isoformat() if slot else None),
            )

    def clear_draft(self, user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))

    # --- записи ----------------------------------------------------------------------------------

    @staticmethod
    def _booking(row: sqlite3.Row) -> Booking:
        return Booking(
            row["id"],
            row["user_id"],
            row["name"],
            row["phone"],
            row["service"],
            datetime.fromisoformat(row["starts_at"]),
            datetime.fromisoformat(row["ends_at"]),
        )

    def day_bookings(self, day: date) -> list[tuple[datetime, datetime]]:
        with self._connect() as db:
            rows = db.execute(
                DAY_BOOKINGS,
                (day.isoformat(),),
            ).fetchall()
        return [
            (datetime.fromisoformat(row["starts_at"]), datetime.fromisoformat(row["ends_at"])) for row in rows
        ]

    def book(
        self, *, user_id: int, name: str, phone: str, service: str, starts_at: datetime, posts: int
    ) -> Booking:
        ends_at = starts_at + SERVICES[service].duration
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")  # блокировка на запись: проверка и вставка — одна операция
            try:
                existing = db.execute(
                    "SELECT * FROM bookings WHERE status = 'active' AND user_id = ? AND starts_at = ?",
                    (user_id, starts_at.isoformat()),
                ).fetchone()
                if existing is not None:  # то же событие пришло второй раз — запись уже есть
                    db.execute("COMMIT")
                    return self._booking(existing)
                rows = db.execute(
                    DAY_BOOKINGS,
                    (starts_at.date().isoformat(),),
                ).fetchall()
                intervals = [
                    (datetime.fromisoformat(r["starts_at"]), datetime.fromisoformat(r["ends_at"]))
                    for r in rows
                ]
                if max_load(intervals, starts_at, ends_at) >= posts:
                    raise SlotTakenError
                cursor = db.execute(
                    "INSERT INTO bookings (user_id, name, phone, service, starts_at, ends_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        name,
                        phone,
                        service,
                        starts_at.isoformat(),
                        ends_at.isoformat(),
                        datetime.now().isoformat(),
                    ),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return Booking(int(cursor.lastrowid or 0), user_id, name, phone, service, starts_at, ends_at)

    def upcoming(self, user_id: int, now: datetime) -> list[Booking]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM bookings WHERE status = 'active' AND user_id = ? AND starts_at >= ? "
                "ORDER BY starts_at",
                (user_id, now.isoformat()),
            ).fetchall()
        return [self._booking(row) for row in rows]

    def cancel(self, booking_id: int, user_id: int) -> Booking | None:
        """Отменяет только свою активную запись; чужую или уже отменённую — нет."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM bookings WHERE id = ? AND user_id = ? AND status = 'active'",
                (booking_id, user_id),
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        return self._booking(row)
