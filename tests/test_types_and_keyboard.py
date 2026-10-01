"""Разбор событий и проверка клавиатур до отправки."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from maxkit import Button, InlineKeyboard, User, parse_update
from maxkit.testing import MaxEmulator


def test_phone_is_taken_from_the_shared_contact(emulator: MaxEmulator) -> None:
    emulator.user_shares_contact(5, "+7 900 123-45-67", name="Анна")

    update = parse_update(emulator.events[-1])

    assert (update.kind, update.user, update.phone) == (
        "message_created",
        User(5, "Анна"),
        "+7 900 123-45-67",
    )


def test_button_press_carries_payload_and_callback_id(emulator: MaxEmulator) -> None:
    callback_id = emulator.user_presses(5, "day:2026-09-14")

    update = parse_update(emulator.events[-1])

    assert (update.kind, update.payload, update.callback_id) == (
        "message_callback",
        "day:2026-09-14",
        callback_id,
    )
    assert update.chat_id == MaxEmulator.dialog(5)


def test_bot_start_has_user_chat_and_payload(emulator: MaxEmulator) -> None:
    emulator.user_starts(5, name="Олег", payload="promo-autumn")

    update = parse_update(emulator.events[-1])

    assert (update.user, update.chat_id, update.payload) == (
        User(5, "Олег"),
        MaxEmulator.dialog(5),
        "promo-autumn",
    )


def test_unknown_event_is_kept_raw() -> None:
    update = parse_update({"update_type": "chat_title_changed", "chat_id": 9})

    assert (update.kind, update.chat_id, update.raw["chat_id"]) == ("chat_title_changed", 9, 9)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(lambda: Button.callback("  ", "x"), "текст", id="пустой-текст"),
        pytest.param(lambda: Button.callback("Да", ""), "payload", id="кнопка-без-payload"),
        pytest.param(lambda: Button.link("Сайт", "ftp://koleso.example"), "http", id="ссылка-не-http"),
        pytest.param(lambda: InlineKeyboard().row(), "Пустой ряд", id="пустой-ряд"),
        pytest.param(lambda: InlineKeyboard().attachment(), "нет ни одной кнопки", id="пустая-клавиатура"),
    ],
)
def test_keyboard_mistakes_are_caught_before_sending(build: Callable[[], object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()
