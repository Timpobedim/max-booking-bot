"""Диалог записи целиком: клиенты нажимают кнопки в эмуляторе MAX, бот отвечает."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from maxkit import MaxApi, parse_update
from maxkit.testing import MaxEmulator
from tyre_bot.bot import build_router
from tyre_bot.settings import Settings
from tyre_bot.storage import Storage

NOW = datetime(2026, 9, 14, 8, 0)  # понедельник, до открытия
MASTERS_CHAT = 777


class Harness:
    """Прогоняет через бота всё, что клиенты успели «нажать» в эмуляторе."""

    def __init__(self, emulator: MaxEmulator, api: MaxApi, tmp_path: Path) -> None:
        settings = Settings(
            _env_file=None,
            max_token="x",
            admin_chat_id=MASTERS_CHAT,
            database=tmp_path / "db.sqlite3",
            posts=1,
        )  # type: ignore[call-arg]
        self.emulator, self.api = emulator, api
        self.router = build_router(Storage(settings.database), settings, now=lambda: NOW)
        self.seen = 0

    def process(self) -> None:
        for event in self.emulator.events[self.seen :]:
            assert self.router.dispatch(self.api, parse_update(event))
        self.seen = len(self.emulator.events)

    def to(self, user_id: int) -> list[dict[str, Any]]:
        return [
            message for message in self.emulator.sent if message.get("chat_id") == MaxEmulator.dialog(user_id)
        ]

    @staticmethod
    def buttons(message: dict[str, Any]) -> list[tuple[str, str | None]]:
        keyboard = next(item for item in message.get("attachments", []) if item["type"] == "inline_keyboard")
        return [
            (button["text"], button.get("payload"))
            for row in keyboard["payload"]["buttons"]
            for button in row
        ]

    def choose(self, user_id: int, *payloads: str, name: str = "Анна") -> None:
        for payload in payloads:
            self.emulator.user_presses(user_id, payload, name=name)
        self.process()


@pytest.fixture
def bot(emulator: MaxEmulator, api: MaxApi, tmp_path: Path) -> Harness:
    return Harness(emulator, api, tmp_path)


PUNCTURE_AT_NINE = ("service:puncture", "day:2026-09-14", "slot:2026-09-14T09:00:00")


def test_client_books_from_greeting_to_confirmation(bot: Harness, emulator: MaxEmulator) -> None:
    emulator.user_starts(1, name="Анна")
    bot.process()
    greeting = bot.to(1)[-1]
    assert greeting["text"].startswith("Здравствуйте!")
    assert ("Ремонт прокола — 800 ₽", "service:puncture") in bot.buttons(greeting)

    bot.choose(1, "service:puncture")
    days = bot.to(1)[-1]
    assert days["text"].startswith("Ремонт прокола: около 30 минут, 800 ₽.")
    assert bot.buttons(days)[0] == ("Пн 14.09", "day:2026-09-14")

    bot.choose(1, "day:2026-09-14")
    slots = bot.to(1)[-1]
    assert slots["text"] == "Свободное время, Пн 14.09:"
    assert bot.buttons(slots)[0] == ("09:00", "slot:2026-09-14T09:00:00")

    bot.choose(1, "slot:2026-09-14T09:00:00")
    assert bot.buttons(bot.to(1)[-1]) == [("Поделиться номером", None)]

    emulator.user_shares_contact(1, "8 (900) 123-45-67", name="Анна")
    bot.process()
    assert bot.to(1)[-1]["text"].startswith("Готово! Вы записаны: Ремонт прокола, Пн 14.09 в 09:00.")
    assert emulator.sent[-1] == {
        "chat_id": MASTERS_CHAT,
        "text": "Новая запись: Ремонт прокола, Пн 14.09 в 09:00. Анна, +79001234567",
        "notify": True,
        "mid": emulator.sent[-1]["mid"],
    }
    assert [answer["notification"] for answer in emulator.answers] == ["Ремонт прокола", "Пн 14.09", "09:00"]


def test_second_client_cannot_take_the_busy_slot(bot: Harness, emulator: MaxEmulator) -> None:
    bot.choose(1, *PUNCTURE_AT_NINE)
    bot.choose(2, *PUNCTURE_AT_NINE, name="Олег")  # оба увидели 9:00 свободным

    emulator.user_shares_contact(1, "+79001112233", name="Анна")
    emulator.user_shares_contact(2, "+79004445566", name="Олег")
    bot.process()

    refused, offer = bot.to(2)[-2:]
    assert refused["text"] == "Это время только что заняли. Выберите другое:"
    assert bot.buttons(offer)[0] == ("09:30", "slot:2026-09-14T09:30:00")  # подъёмник один — 9:00 больше нет


def test_wrong_phone_is_asked_again(bot: Harness, emulator: MaxEmulator) -> None:
    bot.choose(1, *PUNCTURE_AT_NINE)

    emulator.user_says(1, "завтра после обеда")
    bot.process()

    assert bot.to(1)[-1]["text"].startswith("Не похоже на номер телефона")


def test_client_sees_and_cancels_the_booking(bot: Harness, emulator: MaxEmulator) -> None:
    bot.choose(1, *PUNCTURE_AT_NINE)
    emulator.user_shares_contact(1, "+79001112233", name="Анна")
    emulator.user_says(1, "/my")
    bot.process()
    mine = bot.to(1)[-1]
    assert mine["text"] == "Ваши записи:\n• Ремонт прокола, Пн 14.09 в 09:00"
    assert bot.buttons(mine) == [("Отменить: Пн 14.09 09:00", "cancel:1")]

    bot.choose(1, "cancel:1")
    assert bot.to(1)[-1]["text"].startswith("Запись отменена: Ремонт прокола, Пн 14.09 в 09:00.")
    assert emulator.sent[-1]["text"] == "Отмена: Ремонт прокола, Пн 14.09 в 09:00. Анна, +79001112233"

    bot.choose(2, "cancel:1", name="Олег")  # чужую запись отменить нельзя
    assert emulator.answers[-1]["notification"] == "Запись уже отменена"
