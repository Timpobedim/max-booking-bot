"""Long polling: порядок событий, маркер после перезапуска, сбои сети, неверный токен."""

from __future__ import annotations

from pathlib import Path

import pytest

from maxkit import MaxApi, MaxApiError
from maxkit.longpoll import LongPoll
from maxkit.testing import MaxEmulator


def test_events_come_in_order_and_the_marker_survives_restart(
    api: MaxApi, emulator: MaxEmulator, tmp_path: Path
) -> None:
    marker = tmp_path / "bot.marker"
    emulator.user_says(1, "/start")
    emulator.user_presses(1, "service:season")
    events = LongPoll(api, marker_file=marker).listen()

    first, second = next(events), next(events)
    emulator.user_says(1, "ещё вопрос")
    third = next(events)  # пачка из двух событий обработана — маркер записан

    assert (first.text, second.payload, third.text) == ("/start", "service:season", "ещё вопрос")
    assert marker.read_text(encoding="utf-8") == "2"
    restarted = LongPoll(api, marker_file=marker).listen()
    assert (
        next(restarted).text == "ещё вопрос"
    )  # после перезапуска — с того же места, без повтора первых двух


def test_network_failures_do_not_stop_listening(api: MaxApi, emulator: MaxEmulator) -> None:
    emulator.user_says(1, "Добрый день")
    emulator.fail_next = [503] * 4  # клиент исчерпал повторы — пауза и новая попытка уже в цикле
    paused: list[float] = []

    update = next(LongPoll(api, sleep=paused.append).listen())

    assert update.text == "Добрый день"
    assert paused == [1.0]


def test_wrong_token_stops_listening(emulator: MaxEmulator) -> None:
    with (
        MaxApi("wrong-token", transport=emulator.transport(), sleep=lambda _: None) as api,
        pytest.raises(MaxApiError, match="401"),
    ):
        next(LongPoll(api).listen())
